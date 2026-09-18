# Copyright (c) 2026 Jaime C.
#
# Use of this source code is governed by an MIT-style
# license that can be found in the LICENSE file or at
# https://opensource.org/licenses/MIT.

"""A MuJoCo-driven ``RobotBackend``: real dynamics behind the skill API (D34).

This is the *Sim* half of the ``Mock | Sim (MuJoCo) | Real`` ladder.  It loads
the robot MJCF through
:func:`robot_description.mjcf_model.load_mjcf_model_with_scene`, owns a
``mujoco.MjData``, and reports a ``robot_skills`` :class:`Observation` read
from the simulator's state -- the same wire shape the Mock reports, so brain
code and the ``robot_mcp`` server never learn which backend is underneath
(invariant 2 / decision D9).

Decision D34 (in-process MuJoCo): the simulator runs in this process via the
Python ``mujoco`` binding; there is no ROS 2 control stack (that is PR8b and
the ``dfki-ric`` sim, out of scope here).  It therefore keeps the "no ROS
import at runtime" invariant (D30 / issue #99) exactly like the Mock: it
imports ``mujoco`` but never ``rclpy`` / ``ament_index_python``.

**World objects (R-1, PR4 R2).**  A compiled ``MjModel`` cannot gain bodies
post-compile, so the scene-aware build splices each world object into the
merged MJCF as a ``<body>`` *before* compilation -- reusing the PR7 merge
machinery via :func:`load_mjcf_model_with_scene` (see that function's
docstring for the seam).  Since PR4 the body form depends on ``graspable``:
**non-graspable** objects (``counter_1``, ``sofa_1``) stay static, joint-less,
welded bodies that never receive dynamics (no gravity collapse, no drift); a
**graspable** object becomes a **free-jointed movable body** with a small
inertial and ``gravcomp="1"`` -- no floor, no contact physics in this PR -- so
``grasp``/``place`` can drive its pose by writing the free joint's qpos (R2
"re-parent-equivalent").  ``mj_resetData`` restores every free joint to its
``qpos0`` (the body's seed ``pos``), so ``reset`` re-seeds the scene exactly.

**Frame mapping (R-2).**  At the seed, the base sits at the world origin, which
IS the seed map's ``start_location`` (``charger`` at (0,0,0)); MJCF +z == map
+z.  So the map frame and the MuJoCo world frame are the same frame and the
overlay is the identity: an object's seed ``Pose.position`` maps directly onto
its ``<body pos=...>``, and :meth:`get_observation` reports the body's
``xpos``/``xquat`` verbatim as the map coordinate.  Once the base free joint
moves it (PR2 ``navigate_to``), the world frame still *is* the map frame -- the
base's reported pose is just no longer the origin.

**Posture on reset (R-3, PR4).**  ``reset`` homes the robot: wheels at
velocity 0, ``column_lift`` set to the seed ``start_column_height`` with its
position actuator commanded to the same value so the servo *holds* it, arms at
joint zero and grippers OPEN.  ``column_height`` is read back from the
``column_lift`` qpos slot, so it always reports what the simulated lift
actually is.  The **graspable objects' free joints are excluded from the home
joint sweep**: sweeping them to zero would teleport the objects to the origin,
and ``mj_resetData`` has already restored them to their seed ``qpos0``.
``reset`` also clears all grasp book-keeping.

**Base free joint (PR2, R4/R5).**  ``navigate_to`` needs to move the base, but the
fused trunk has no own joint.  The scene build therefore wraps the robot in a
6-DOF free-jointed ``base_link`` body (see ``robot_description.mjcf_model``) that
``navigate_to`` *teleports* to a named location's reference pose (R3 -- no
``mj_step``, no wheel dynamics yet).  Because the free joint sits first in qpos
and shifts every joint/actuator index, every inner lookup here is **by name**
(R5): ``base_free`` qpos/dof address, ``base_link`` body id, wheel qpos/ctrl
pairs, and the ``column_lift`` joint's own travel range.

**Skills (PR2).**  :meth:`execute` implements ``navigate_to`` and
``extend_column`` (see their handlers).  ``extend_column`` mirrors the Mock's
reject, don't-clamp semantics (R2): a height outside the column's travel is
refused with ``OUT_OF_RANGE`` and the sim is left unchanged; the safety layer
(robot_mcp) owns clamping.  (PR3/PR4 complete the skill set; a skill this
backend does not implement would be ``status=failed`` with
:class:`~robot_skills.FailureCode.UNSUPPORTED_SKILL` and an unchanged world --
the ABC's total contract; see :meth:`execute`.)

**Skills (PR3).**  :meth:`execute` additionally implements ``move_gripper``,
``open_gripper`` and ``close_gripper``.  ``move_gripper`` targets the *reported*
gripper frame (jaw midpoint + upper-jaw orientation, R2) and solves for the
five arm joints with :func:`~robot_backends.ik.solve_ik`; a pose the arm cannot
reach (the solver's residual never falls below tolerance) is refused with
``OUT_OF_REACH``, whose reason reuses :class:`~robot_backends.mock_world.RobotModel`
so it stays semantically identical to the Mock's.  ``open_gripper`` /
``close_gripper`` write the driven joint *and* its independent mirror (R5) and
never refuse (D19 idempotence).  All three write qpos **and** the matching
position actuators (servo hold) then ``mj_forward`` -- no ``mj_step`` (R4).

**Skills (PR4).**  :meth:`execute` implements ``grasp`` and ``place``,
mirroring :mod:`robot_backends.mock_backend`'s semantics *exactly* (same
failure codes and reason strings, R3): the same validation order (unknown id ->
not graspable -> already held -> gripper occupancy), the same left-first
reach-aware side pick for ``grasp`` and the same non-reach-aware side pick for
``place``.  The one deliberate difference is the **reach oracle**: the Mock
tests a sphere of ``reach_radius`` around each shoulder, while the sim asks
:func:`~robot_backends.ik.solve_ik` in **position-only mode** (R6) -- the
``move_gripper`` full-pose test is unsatisfiable for the seed objects, since
the 5-DOF arm cannot hold an identity orientation there.  On grasp the object
is attached by book-keeping (attach-on-close, R1) with the grasp offset
(object pose in the gripper frame); :meth:`_carry_held_objects` re-writes the
held object's free-joint qpos to ``gripper_frame ∘ offset`` after every skill,
exactly mirroring ``mock_backend._carry_held_objects``.  No ``mj_step`` on the
skill path (R2); the geometry below stays a placeholder for roadmap #4's
visuals.
"""

from __future__ import annotations

from typing import Mapping, NoReturn

import mujoco
import numpy as np

from robot_backends.ik import solve_ik
from robot_backends.interface import RobotBackend
from robot_backends.mock_world import RobotModel
from robot_description.mjcf_model import load_mjcf_model_with_scene
from robot_skills import (
    CloseGripper,
    ExtendColumn,
    FailureCode,
    Grasp,
    GripperObservation,
    GripperState,
    MoveGripper,
    NavigateTo,
    Observation,
    OpenGripper,
    Place,
    Point,
    Pose,
    Quaternion,
    RobotState,
    SceneObject,
    Side,
    SIDE_ORDER,
    Skill,
    SkillResult,
)
from robot_world import default_seed_document, WorldDocument

__all__ = ['MuJoCoBackend']


#: Column joint name; both its qpos slot and its matching position actuator
#: are looked up by name at init so the index never has to be hand-kept here.
_COLUMN_JOINT = 'column_lift'

#: The free-jointed body ``mjcf_model`` wraps the base trunk in (PR2, R4) and
#: the joint that lets ``navigate_to`` teleport it.  Name-based lookups (R5):
#: the free joint sits first in qpos (MuJoCo numbers free-joint qpos first),
#: pushing the wheel/column/arm indices, so nothing here may assume position 0,
#: 1, 2 is a wheel.  See ``robot_description.mjcf_model`` for the wrap.
_BASE_BODY = 'base_link'
_BASE_FREE = 'base_free'

#: Wheel joints are velocity-controlled (they see no position actuator); these
#: are /excluded/ from the position-actuator home sweep on reset.
_WHEEL_JOINTS = ('base_left_wheel', 'base_back_wheel', 'base_right_wheel')

#: A gripper's reported pose is the symmetric midpoint of its two open jaws
#: (there is no single "palm" body -- the URDF gripper base is folded into the
#: wrist by ``fusestatic``).  These name patterns select the jaw bodies.
_JAW_UPPER = 'gripper_upper_jaw_link'
_JAW_LOWER = 'gripper_lower_jaw_link'

#: The five revolute arm joints per side, in kinematic order (base -> tip).
#: A side's joint is named ``<side>_<suffix>``; :meth:`MuJoCoBackend._arm_fk`
#: writes exactly these and nothing else (R1), so the IK solve variables cannot
#: leak into the base, column or jaws.
_ARM_JOINT_SUFFIXES = (
    'shoulder_pan', 'shoulder_lift', 'elbow_flex', 'wrist_flex', 'wrist_roll')


class _SkillRefused(Exception):
    """Internal signal: a skill is refused up front; the sim is unchanged.

    Mirrors the Mock's refusal discipline (mock_backend._SkillRefused): a
    handler validates before it mutates, and :meth:`MuJoCoBackend.execute`
    turns the raise into a failed :class:`~robot_skills.result.SkillResult` with
    an attributable :class:`FailureCode`.  A refused skill therefore never
    leaves the simulation half-changed.
    """

    def __init__(self, code: FailureCode, reason: str) -> None:
        super().__init__(reason)
        self.code = code
        self.reason = reason


#: Primitive scene geometry per semantic label (PR1 v1: no meshes).
#:
#: Each entry renders one static ``<geom>`` centred on the object's seed 3D
#: point. MuJoCo ``size`` is **half-extents** for ``box`` = (hx, hy, hz) and
#: (radius, half-height) for ``cylinder``; ``sphere`` size is the radius.  The
#: robot and neighbouring objects sit metres from these poses, so the crude
#: sizes below never touch the robot during a step; they are placeholders
#: until roadmap #4 tunes visuals.  ``contype/conaffinity=0`` makes every scene
#: geom collision-inert (R-1/Q4), so a step can never perturb an object via
#: contact either.
#:
#: ``(geom_type, size_string, rgba)``
_PRIMITIVE_GEOMS: Mapping[str, tuple[str, str, str]] = {
    'mug': ('cylinder', '0.035 0.05', '0.42 0.26 0.15 1'),
    'plate': ('cylinder', '0.10 0.008', '0.85 0.85 0.85 1'),
    'bowl': ('cylinder', '0.075 0.04', '0.30 0.60 0.55 1'),
    'counter': ('box', '0.70 0.30 0.25', '0.62 0.58 0.52 1'),
    'book': ('box', '0.13 0.02 0.09', '0.20 0.30 0.70 1'),
    'cup': ('cylinder', '0.03 0.05', '0.95 0.95 0.90 1'),
    'sofa': ('box', '0.50 0.40 0.35', '0.55 0.35 0.20 1'),
}

#: Fallback for an unknown label: a small neutral sphere, so an added world
#: object still appears in the simulation rather than silently vanishing.
_FALLBACK_GEOM: tuple[str, str, str] = ('sphere', '0.05', '0.60 0.60 0.60 1')


def _geom_attrs(label: str) -> tuple[str, str, str]:
    """Return ``(type, size, rgba)`` for ``label``, falling back to a sphere."""
    return _PRIMITIVE_GEOMS.get(label, _FALLBACK_GEOM)


#: Mass of a graspable object's free body.  Must exceed ``mjMINVAL`` (MuJoCo
#: rejects a zero/near-zero mass), the same pattern ``_wrap_base_freejoint``
#: uses for the base; ``gravcomp="1"`` keeps the free body from falling (there
#: is no floor in this PR).
_OBJECT_MASS = 0.1

#: Isotropic inertia for a graspable object's free body (see ``_OBJECT_MASS``).
_OBJECT_INERTIA = 0.001


def _world_bodies_xml(document: WorldDocument) -> str:
    """Render ``document.objects`` as ``<body>`` blocks for the worldbody.

    Each object becomes one body named by its ``object_id`` (seed ids are
    alphanumeric+underscore, valid MuJoCo names) and placed at its seed pose.
    Its label selects a primitive geom via :func:`_geom_attrs`.

    Since PR4 (R2) the body form depends on ``graspable``:

    * **non-graspable** objects stay static, joint-less, welded bodies (no
      dynamics, no drift -- an object's world frame equals its seed pose);
    * **graspable** objects become free-jointed movable bodies (``freejoint``
      named ``<object_id>_free``) carrying a small inertial and
      ``gravcomp="1"``, so ``grasp``/``place`` can drive their pose by writing
      the free joint's qpos.  The free joint's ``qpos0`` defaults from the
      body ``pos``, so ``mj_resetData`` restores the seed pose on ``reset``.

    Geoms stay collision-inert (``contype/conaffinity=0``): no contact physics
    in this PR.
    """
    blocks = []
    for item in document.objects:
        geom_type, size, rgba = _geom_attrs(item.label)
        p = item.pose.position
        if item.graspable:
            blocks.append(
                f'<body name="{item.object_id}" pos="{p.x} {p.y} {p.z}" gravcomp="1">\n'
                f'  <freejoint name="{item.object_id}_free"/>\n'
                f'  <inertial pos="0 0 0" mass="{_OBJECT_MASS}" '
                f'diaginertia="{_OBJECT_INERTIA} {_OBJECT_INERTIA} {_OBJECT_INERTIA}"/>\n'
                f'  <geom type="{geom_type}" size="{size}" rgba="{rgba}" '
                'contype="0" conaffinity="0"/>\n'
                '</body>'
            )
        else:
            blocks.append(
                f'<body name="{item.object_id}" pos="{p.x} {p.y} {p.z}">\n'
                f'  <geom type="{geom_type}" size="{size}" rgba="{rgba}" '
                'contype="0" conaffinity="0"/>\n'
                '</body>'
            )
    return '\n'.join(blocks) if blocks else ''


class MuJoCoBackend(RobotBackend):
    """A ``RobotBackend`` that reports real simulated state from MuJoCo.

    PR2 (issue #101) implements two motion skills via the simulator itself:

    * ``navigate_to`` -- home the 6-DOF base free joint to a named location's
      reference pose (a **teleport**, no ``mj_step`` / no wheel dynamics, R3);
    * ``extend_column`` -- set the prismatic column joint target, reading the
      clamped height back from ``mjData`` (R2).

    PR3 adds ``move_gripper`` (IK over the five arm joints) and
    ``open_gripper``/``close_gripper`` (driven + mirror jaw joints).  PR4 adds
    ``grasp``/``place`` (position-only-IK reach, attach-on-close book-keeping,
    object free-joint poses), mirroring the Mock's semantics.  A skill that
    cannot be carried out returns ``status=failed`` with an attributable
    :class:`~robot_skills.FailureCode` and leaves the world unchanged -- the
    ABC's *total* contract; :meth:`execute` only raises for a non-``Skill``.
    """

    def __init__(
        self,
        document: WorldDocument | None = None,
        *,
        step_dt: float = 0.002,
    ) -> None:
        """Create a backend over ``document`` (the shipped apartment by default).

        The scene is **compiled in** at construction: world objects are welded
        into the MJCF before compile (R-1), so :meth:`reset` never rebuilds the
        model -- it only re-homes the robot joints (the scene is immovable).
        The base is free-jointed (see ``robot_description.mjcf_model``), so its
        qpos/body ids are resolved **by name** here (R5): adding the free joint
        shifts every joint/actuator index, so nothing below may assume a fixed
        slot.  ``where the robot is`` (:attr:`_location`) starts at the seed's
        ``start_location`` and is updated by ``navigate_to``.
        """
        if document is not None and not isinstance(document, WorldDocument):
            raise TypeError(
                f'document must be a WorldDocument, got {type(document).__name__}')
        self._document = document if document is not None else default_seed_document()
        self._step_dt = float(step_dt)

        self._model: mujoco.MjModel = load_mjcf_model_with_scene(
            _world_bodies_xml(self._document))
        self._data: mujoco.MjData = mujoco.MjData(self._model)

        # Name-based lookups (R5): the free joint shifts every qpos index, so
        # nothing is assumed to live at slot 0..N.
        self._base_free_qposadr = self._joint_qposadr(_BASE_FREE)
        self._base_free_dofadr = self._joint_dofadr(_BASE_FREE)
        self._base_body = self._body_id(_BASE_BODY)
        self._column_qpos_adr = self._joint_qposadr(_COLUMN_JOINT)
        self._column_ctrl = self._actuator_id(_COLUMN_JOINT)
        # The wheel pairs (joint qpos index, velocity-actuator id) by name.
        self._wheel: dict[str, tuple[int, int]] = {
            name: (self._joint_qposadr(name), self._actuator_id(name))
            for name in _WHEEL_JOINTS
        }
        # The prismatic column's travel range, taken from the joint it drives
        # (R2 preferred source) so the backend agrees with the MJCF it compiled.
        self._column_range = self._joint_range(_COLUMN_JOINT)

        # Arm + gripper joints, by name (R5): the five arm joints keep the IK
        # solve variables (qpos adr + matching position actuator), and each
        # side's gripper pairs the *driven* joint with its independent mirror
        # (MuJoCo drops the URDF mimic -- R5 -- so both must be commanded).
        self._arm_joints: dict[Side, dict[str, tuple[int, int]]] = {}
        self._gripper_joints: dict[Side, dict[str, tuple[int, int]]] = {}
        self._arm_ranges: dict[Side, tuple[tuple[float, float], ...]] = {}
        self._gripper_ranges: dict[Side, tuple[tuple[float, float], tuple[float, float]]] = {}
        for side in SIDE_ORDER:
            names = [f'{side.value}_{suffix}' for suffix in _ARM_JOINT_SUFFIXES]
            self._arm_joints[side] = {
                name: (self._joint_qposadr(name), self._actuator_id(name))
                for name in names
            }
            self._arm_ranges[side] = tuple(
                self._joint_range(name) for name in names)
            driven = f'{side.value}_gripper'
            mirror = f'{driven}_mirror'
            self._gripper_joints[side] = {
                'driven': (self._joint_qposadr(driven), self._actuator_id(driven)),
                'mirror': (self._joint_qposadr(mirror), self._actuator_id(mirror)),
            }
            self._gripper_ranges[side] = (
                self._joint_range(driven), self._joint_range(mirror))

        # Arms + grippers + mirrors are returned to their home posture on reset
        # (defined by ``_HOME_JOINTS``): arm joints to zero, gripper jaws to
        # OPEN (R6).  The base free joint is /excluded/ from the joint sweep
        # (R5): it is homed to the start-location pose instead, and the
        # grippers are written explicitly (their home is not joint zero).
        # The graspable objects' free joints are /excluded/ too (PR4 reset
        # trap): sweeping them to zero would teleport every object to the
        # origin, and ``mj_resetData`` has already restored them to the seed
        # ``qpos0`` the body ``pos`` gives them.
        object_free_joints = tuple(
            f'{item.object_id}_free'
            for item in self._document.objects if item.graspable)
        self._home_joints = self._joint_ids_excluding(
            _WHEEL_JOINTS + (_COLUMN_JOINT, _BASE_FREE) + object_free_joints)
        self._home_position_actuators = self._actuator_ids_excluding(
            _WHEEL_JOINTS + (_COLUMN_JOINT,))

        # Where the robot is (PR2): starts at the seed start_location; the
        # base free joint's world pose is the single source of physical truth,
        # but Observation.location is the semantic map name, so track it here.
        self._location: str = self._document.start_location

        # World object -> body id table for observation reads.
        self._object_body: dict[str, int] = {}
        for item in self._document.objects:
            body_id = self._body_id(item.object_id)
            self._object_body[item.object_id] = body_id

        # Graspable objects carry a ``<object_id>_free`` free joint (R2).  Its
        # qpos slot is resolved by name (R5) so grasp/place/carry can write an
        # object's world pose directly.  Non-graspable objects are welded (no
        # joint), so they are absent from this table.
        self._object_free_joint: dict[str, int] = {}
        self._object_free_qpos: dict[str, int] = {}
        for item in self._document.objects:
            if not item.graspable:
                continue
            name = f'{item.object_id}_free'
            self._object_free_joint[item.object_id] = self._joint_id(name)
            self._object_free_qpos[item.object_id] = self._joint_qposadr(name)

        # Per-side grasp book-keeping (R4): the object each gripper holds and
        # the object's pose expressed in that gripper's frame at grasp time.
        # Cleared by :meth:`reset`.
        self._held_object: dict[Side, str | None] = dict.fromkeys(SIDE_ORDER)
        self._held_offset: dict[Side, Pose | None] = dict.fromkeys(SIDE_ORDER)

        # Gripper jaw body ids, keyed by Side.
        self._jaw_upper: dict[Side, int] = {}
        self._jaw_lower: dict[Side, int] = {}
        for side in SIDE_ORDER:
            body_id = self._body_id(f'{side.value}_{_JAW_UPPER}')
            self._jaw_upper[side] = body_id
            self._jaw_lower[side] = self._body_id(f'{side.value}_{_JAW_LOWER}')

        # The shared body-constants model (R3): the Mock's kinematic stand-in,
        # reused wholesale so the refusal reason matches the Mock's exactly.
        self._robot = RobotModel()

        # Per-side arm qpos snapshots for the IK solver's restore contract.
        self._arm_snapshot: dict[Side, np.ndarray] = {
            side: np.zeros(len(_ARM_JOINT_SUFFIXES)) for side in SIDE_ORDER}

    # -- model-role resolution ---------------------------------------------

    def _body_id(self, name: str) -> int:
        body_id = mujoco.mj_name2id(self._model, mujoco.mjtObj.mjOBJ_BODY, name)
        if body_id < 0:
            raise ValueError(f'no body named {name!r} in the MuJoCo model')
        return int(body_id)

    def _joint_qposadr(self, name: str) -> int:
        joint_id = mujoco.mj_name2id(self._model, mujoco.mjtObj.mjOBJ_JOINT, name)
        if joint_id < 0:
            raise ValueError(f'no joint named {name!r} in the MuJoCo model')
        return int(self._model.jnt_qposadr[joint_id])

    def _joint_id(self, name: str) -> int:
        joint_id = mujoco.mj_name2id(self._model, mujoco.mjtObj.mjOBJ_JOINT, name)
        if joint_id < 0:
            raise ValueError(f'no joint named {name!r} in the MuJoCo model')
        return int(joint_id)

    def _joint_dofadr(self, name: str) -> int:
        """Return the first velocity (dof) index of the joint named ``name``."""
        joint_id = mujoco.mj_name2id(self._model, mujoco.mjtObj.mjOBJ_JOINT, name)
        if joint_id < 0:
            raise ValueError(f'no joint named {name!r} in the MuJoCo model')
        return int(self._model.jnt_dofadr[joint_id])

    def _joint_range(self, name: str) -> tuple[float, float]:
        """Return the ``(min, max)`` travel limit of the joint named ``name``.

        The prismatic column's limits come straight from the compiled MJCF
        (``jnt_range``), so :meth:`_extend_column` agrees with the model it
        compiled (R2).
        """
        joint_id = mujoco.mj_name2id(self._model, mujoco.mjtObj.mjOBJ_JOINT, name)
        if joint_id < 0:
            raise ValueError(f'no joint named {name!r} in the MuJoCo model')
        lo = float(self._model.jnt_range[joint_id, 0])
        hi = float(self._model.jnt_range[joint_id, 1])
        return (lo, hi)

    def _actuator_id(self, name: str) -> int:
        aid = mujoco.mj_name2id(self._model, mujoco.mjtObj.mjOBJ_ACTUATOR, name)
        if aid < 0:
            raise ValueError(f'no actuator named {name!r} in the MuJoCo model')
        return int(aid)

    def _joint_ids_excluding(self, excluded: tuple[str, ...]) -> list[int]:
        """Return every joint id whose name is not in ``excluded``."""
        names = set(excluded)
        return [
            jid for jid in range(self._model.njnt)
            if mujoco.mj_id2name(
                self._model, mujoco.mjtObj.mjOBJ_JOINT, jid) not in names
        ]

    def _actuator_ids_excluding(self, excluded: tuple[str, ...]) -> list[int]:
        """Return every actuator id whose name is not in ``excluded``."""
        names = set(excluded)
        return [
            aid for aid in range(self._model.nu)
            if mujoco.mj_id2name(
                self._model, mujoco.mjtObj.mjOBJ_ACTUATOR, aid) not in names
        ]

    # -- RobotBackend ------------------------------------------------------

    def reset(self) -> Observation:
        """Restore the seed scene and the seed posture, and observe the result.

        Object poses are invariant (welded and immovable -- R-1), so reset
        needs no scene restore; it re-homes the robot joints per R-3 and
        re-observes.  The base free joint is homed to the ``start_location``
        reference pose (not joint zero) and ``_location`` is reset to the seed
        start, so a backend that has since navigated away comes home on reset.
        """
        data = self._data
        mujoco.mj_resetData(self._model, data)

        # Base free joint -> the seed start-location pose (R5).  This moves
        # the whole kinematic subtree home, so the robot always comes up at
        # the map's ``start_location`` regardless of prior navigation.
        self._set_base_pose(
            self._document.locations[self._document.start_location])

        # Wheels: velocity actuators commanded to 0 (name-based, R5).
        for name in _WHEEL_JOINTS:
            qpos_adr, ctrl = self._wheel[name]
            data.qpos[qpos_adr] = 0.0
            data.ctrl[ctrl] = 0.0

        # Column: lift to the seed start height AND command its position
        # actuator to the same target so the servo holds it (R-3).
        height = self._document.start_column_height
        data.qpos[self._column_qpos_adr] = height
        data.ctrl[self._column_ctrl] = height

        # Arms: home at joint zero (position actuators -- a ctrl of 0 makes the
        # zero joint the servo's target).  The free joint is excluded from this
        # sweep (R5) -- it was homed above instead.  Grippers are homed OPEN
        # just below (R6): their home is not joint zero.
        for jid in self._home_joints:
            data.qpos[self._model.jnt_qposadr[jid]] = 0.0
        for aid in self._home_position_actuators:
            data.ctrl[aid] = 0.0

        # Grippers home OPEN (R6): the driven jaw at its lower limit and the
        # mirror at the symmetric positive target, each with its position
        # actuator commanded to the same value so the servo holds the posture.
        for side in SIDE_ORDER:
            open_qpos, open_ctrl = self._gripper_target(side, closed=False)
            for key in ('driven', 'mirror'):
                qpos_adr, ctrl_id = self._gripper_joints[side][key]
                data.qpos[qpos_adr] = open_qpos[key]
                data.ctrl[ctrl_id] = open_ctrl[key]

        # Clear all grasp book-keeping: reset homed the gripper jaws OPEN and
        # ``mj_resetData`` re-seeded every object free joint, so nothing is
        # held any more (R4).
        for side in SIDE_ORDER:
            self._held_object[side] = None
            self._held_offset[side] = None

        self._location = self._document.start_location
        mujoco.mj_forward(self._model, data)
        return self.get_observation()

    def step(self, n: int = 1) -> None:
        """Advance the simulation ``n`` fixed timesteps (a test hook, not a seam call).

        Not part of the ``RobotBackend`` interface -- no skill here calls it:
        ``navigate_to`` teleports (no ``mj_step``, R3).  It exists so the
        existing acceptance tests can prove that a real ``mj_step`` leaves the
        (static, welded) scene objects at their seed poses.

        .. note::  the free-jointed base has no floor/contact below it yet (R6),
           so enough real steps will drop it under gravity.  That is a known,
           deferred limitation (PR4 adds floor + wheel dynamics); the servo-hold
           and scene-invariance tests below only assert relative/static
           quantities and remain green.
        """
        for _ in range(n):
            mujoco.mj_step(self._model, self._data, nstep=1)

    def get_observation(self) -> Observation:
        """Return an immutable snapshot of the robot and the scene."""
        # Propagate current joint state to body frames before reading them.
        mujoco.mj_forward(self._model, self._data)
        return Observation(
            robot=RobotState(
                pose=self._base_pose(),
                column_height=float(self._data.qpos[self._column_qpos_adr]),
                grippers=tuple(
                    self._gripper_observation(side) for side in SIDE_ORDER),
                location=self._location,
            ),
            objects=tuple(
                SceneObject(
                    object_id=item.object_id,
                    label=item.label,
                    pose=self._object_pose(item.object_id),
                    graspable=item.graspable,
                    held_by=self._holder_of(item.object_id),
                )
                for item in sorted(
                    self._document.objects, key=lambda o: o.object_id)
            ),
            known_locations=tuple(sorted(self._document.locations)),
        )

    def execute(self, skill: Skill) -> SkillResult:
        """Execute one skill, returning its status and a fresh observation.

        ``NavigateTo`` and ``ExtendColumn`` (PR2), ``MoveGripper`` /
        ``OpenGripper`` / ``CloseGripper`` (PR3) and ``Grasp`` / ``Place``
        (PR4) dispatch to their handlers; any other skill is a clean
        ``status=failed`` with ``UNSUPPORTED_SKILL`` and an unchanged
        observation.  After a successful handler the carried objects are
        re-written to their grippers' frames (:meth:`_carry_held_objects`, R2),
        exactly as ``mock_backend.execute`` does.  Per the ABC's *total*
        contract this never raises for a legal :class:`Skill`, and it only
        raises for something that is not a ``Skill`` at all.
        """
        if not isinstance(skill, Skill):
            raise TypeError(
                f'execute() expects a Skill, got {type(skill).__name__}')
        try:
            if isinstance(skill, NavigateTo):
                note = self._navigate_to(skill)
            elif isinstance(skill, ExtendColumn):
                note = self._extend_column(skill)
            elif isinstance(skill, MoveGripper):
                note = self._move_gripper(skill)
            elif isinstance(skill, Grasp):
                note = self._grasp(skill)
            elif isinstance(skill, Place):
                note = self._place(skill)
            elif isinstance(skill, OpenGripper):
                note = self._open_gripper(skill)
            elif isinstance(skill, CloseGripper):
                note = self._close_gripper(skill)
            else:
                return SkillResult.failure(
                    skill,
                    self.get_observation(),
                    FailureCode.UNSUPPORTED_SKILL,
                    f'the MuJoCo backend does not implement skill {skill.name!r}',
                )
            self._carry_held_objects()
        except _SkillRefused as refusal:
            return SkillResult.failure(
                skill, self.get_observation(), refusal.code, refusal.reason)
        return SkillResult.ok(skill, self.get_observation(), note)

    # -- skills (PR2) ------------------------------------------------------

    def _set_base_pose(self, pose: Pose) -> None:
        """Teleport the base so its world pose is ``pose`` (free-joint qpos + forward).

        Writes the base free joint's qpos from ``pose`` and runs ``mj_forward``
        so body frames reflect the new location.  This is a *teleport* -- no
        ``mj_step``, no wheel dynamics (R3) -- and it moves the whole kinematic
        subtree (wheels, column, arms) as one rigid unit, which is a mobile base
        home, exactly the semantics ``navigate_to`` wants.  The free joint's
        velocities are zeroed so a latent ``mj_step`` does not carry residual
        motion from the move.
        """
        data = self._data
        adr = self._base_free_qposadr
        p, q = pose.position, pose.orientation
        data.qpos[adr + 0] = p.x
        data.qpos[adr + 1] = p.y
        data.qpos[adr + 2] = p.z
        # MuJoCo free-joint quaternion is w-first; our Quaternion is (x,y,z,w).
        data.qpos[adr + 3] = q.w
        data.qpos[adr + 4] = q.x
        data.qpos[adr + 5] = q.y
        data.qpos[adr + 6] = q.z
        # Zero the free joint's 6 velocities (a stationary teleport).
        data.qvel[self._base_free_dofadr:self._base_free_dofadr + 6] = 0.0
        mujoco.mj_forward(self._model, data)

    def _navigate_to(self, skill: NavigateTo) -> str | None:
        """Drive the base to a named location (teleport, mirrors the Mock).

        Unknown location -> ``UNKNOWN_LOCATION`` refusal naming the known
        locations (sorted); known location -> teleport the base free joint to
        the location's reference pose and update :attr:`_location`.  Returning
        to the current location is a successful no-op whose reason notes
        "already at <name>" (mirroring ``mock_backend._navigate_to``).
        """
        pose = self._document.locations.get(skill.location)
        if pose is None:
            known = ', '.join(sorted(self._document.locations))
            raise _SkillRefused(
                FailureCode.UNKNOWN_LOCATION,
                f'unknown location {skill.location!r}; '
                f'known locations: {known}')
        already_there = self._location == skill.location
        self._set_base_pose(pose)
        self._location = skill.location
        return f'already at {skill.location!r}' if already_there else None

    def _extend_column(self, skill: ExtendColumn) -> str | None:
        """Set the lift column height, reporting the height read back from mjData.

        Mirrors the Mock exactly (R2): a height outside the column's travel
        range is refused with ``OUT_OF_RANGE`` (the sim is left unchanged); an
        in-range height sets the column prismatic joint AND commands its
        position actuator to the same target so the servo holds it, exactly as
        :meth:`reset` does for the seed height, then reads the height back from
        the joint's qpos slot.  The "2.0 -> clamp to max" rewrite is the
        *safety layer's* job (robot_mcp), not this backend's (defense-in-depth).
        """
        low, high = self._column_range
        height = skill.height
        if not low <= height <= high:
            raise _SkillRefused(
                FailureCode.OUT_OF_RANGE,
                f'column height {height:.2f} m is outside the column range '
                f'[{low:.2f}, {high:.2f}] m')
        data = self._data
        data.qpos[self._column_qpos_adr] = height
        data.ctrl[self._column_ctrl] = height
        mujoco.mj_forward(self._model, data)
        return None

    def _move_gripper(self, skill: MoveGripper) -> str | None:
        """Move one gripper to a Cartesian pose by solving IK for the arm (PR3).

        The target is the *reported* gripper frame (R2) -- jaw midpoint plus
        upper-jaw orientation -- so a commanded pose and an observed pose can
        never disagree.  ``solve_ik`` returns ``None`` when no start converges,
        which is the honest reachability signal (R3): the skill is then refused
        with ``OUT_OF_REACH`` and a Mock-shaped reason, and ``solve_ik``'s
        ``restore`` contract means the sim is untouched.  On success the five
        arm qpos slots and their position actuators are written to the solved
        values (servo hold) and ``mj_forward`` advances kinematics -- no
        ``mj_step`` (R4).
        """
        side = skill.side
        position = skill.pose.position
        target_rotation = _quat_to_matrix(skill.pose.orientation)
        arm = self._arm_joints[side]
        joint_adrs = self._arm_joint_adrs(side)
        lower = np.array([rng[0] for rng in self._arm_ranges[side]])
        upper = np.array([rng[1] for rng in self._arm_ranges[side]])
        self._snapshot_arm(side)

        solved = solve_ik(
            np.array([position.x, position.y, position.z]),
            target_rotation,
            fk=lambda q: self._arm_fk(side, q),
            joint_adrs=joint_adrs,
            lower=lower,
            upper=upper,
            restore=lambda: self._restore_arm(side),
        )
        if solved is None:
            raise _SkillRefused(
                FailureCode.OUT_OF_REACH, self._unreachable_reason(
                    side, position, 'move the gripper to'))

        data = self._data
        for value, (qpos_adr, ctrl_id) in zip(solved, arm.values()):
            data.qpos[qpos_adr] = float(value)
            data.ctrl[ctrl_id] = float(value)
        mujoco.mj_forward(self._model, data)
        return None

    def _open_gripper(self, skill: OpenGripper) -> str | None:
        """Open one gripper (driven + mirror jaws), idempotently (D19).

        Always succeeds; an already-open gripper says so in an informational
        reason (mirroring ``mock_backend._open_gripper``), otherwise ``None``.
        """
        side = skill.side
        was_open = self._gripper_state(side) is GripperState.OPEN
        self._set_gripper(side, closed=False)
        return f'the {side.value} gripper was already open' if was_open else None

    def _close_gripper(self, skill: CloseGripper) -> str | None:
        """Close one gripper (driven + mirror jaws), idempotently (D19).

        Closing on thin air grips nothing -- ``grasped`` stays ``False`` -- and
        that is reported by the observation rather than raised here (R5).
        """
        side = skill.side
        was_closed = self._gripper_state(side) is GripperState.CLOSED
        self._set_gripper(side, closed=True)
        if was_closed:
            return f'the {side.value} gripper was already closed'
        return None

    # -- skills (PR4): grasp + place ---------------------------------------

    def _grasp(self, skill: Grasp) -> str | None:
        """Close a free gripper around a present, graspable, reachable object.

        Mirrors :meth:`mock_backend.MockBackend._grasp` exactly (R3) -- the same
        validation order, the same failure codes and reason strings -- except
        that "reachable" means a **position-only IK solve** converges (R6), not
        the Mock's sphere test.  On success the arm is driven to the object
        (servo-hold, as :meth:`_move_gripper`), the jaws close, and the object
        is attached by book-keeping (attach-on-close, R1): ``_held_object`` and
        the grasp offset (object pose in the gripper frame).  :meth:`execute`
        then re-writes the object's free-joint qpos via
        :meth:`_carry_held_objects`, so the reported pose jumps to the gripper.
        """
        item = self._find_object(skill.object_id)
        if item is None:
            raise _SkillRefused(
                FailureCode.UNKNOWN_OBJECT,
                f'no object {skill.object_id!r} in the scene; perceived objects: '
                f'{", ".join(sorted(self._object_ids()))}',
            )
        if not item.graspable:
            raise _SkillRefused(
                FailureCode.NOT_GRASPABLE,
                f'object {item.object_id!r} ({item.label}) is not graspable',
            )
        if self._holder_of(item.object_id) is not None:
            raise _SkillRefused(
                FailureCode.OBJECT_ALREADY_HELD,
                f'object {item.object_id!r} is already held by the '
                f'{self._holder_of(item.object_id).value} gripper',
            )
        object_pose = self._object_pose(item.object_id)
        side = self._resolve_grasping_side(
            skill.side, object_pose.position, f'grasp {item.object_id!r}')

        solved = self._position_ik(side, object_pose.position)
        if solved is None:  # unreachable: _resolve_grasping_side guarantees a solve
            raise _SkillRefused(
                FailureCode.OUT_OF_REACH, self._unreachable_reason(
                    side, object_pose.position, f'grasp {item.object_id!r}'))
        self._write_arm(side, solved)
        self._set_gripper(side, closed=True)

        self._held_object[side] = item.object_id
        self._held_offset[side] = _offset_between(
            self._gripper_pose(side), object_pose)
        return None

    def _place(self, skill: Place) -> str | None:
        """Put the held object down at a reachable pose and open the gripper.

        Mirrors :meth:`mock_backend.MockBackend._place` exactly (R3): side pick
        is non-reach-aware, an over-far target refuses ``OUT_OF_REACH``, and on
        success the object's pose becomes *exactly* ``skill.pose`` (position and
        orientation), the hold is cleared and the jaws open.
        """
        side = self._resolve_holding_side(skill.side)
        held_id = self._held_object[side]
        if held_id is None:  # unreachable: _resolve_holding_side guarantees a load
            raise _SkillRefused(
                FailureCode.GRIPPER_EMPTY,
                f'the {side.value} gripper is empty, there is nothing to place',
            )
        target = skill.pose.position
        solved = self._position_ik(side, target)
        if solved is None:
            raise _SkillRefused(
                FailureCode.OUT_OF_REACH, self._unreachable_reason(
                    side, target, f'place {held_id!r}'))

        self._write_arm(side, solved)
        # Detach: the object's free-joint qpos becomes exactly the commanded
        # pose (R2), and the book-keeping clears so carry stops tracking it.
        self._set_object_pose(held_id, skill.pose)
        self._held_object[side] = None
        self._held_offset[side] = None
        self._set_gripper(side, closed=False)
        return f'released {held_id!r} from the {side.value} gripper'

    def _resolve_grasping_side(
        self, requested: Side | None, target: Point, action: str,
    ) -> Side:
        """Pick which gripper grasps ``target`` (Mock's reach-aware order, R3).

        With a side named, that side must be free and able to reach (else
        ``OUT_OF_REACH``).  With no side named, prefer the first side in
        ``SIDE_ORDER`` that is *both* free and reachable; if none is reachable,
        refuse ``OUT_OF_REACH`` for the first free side.  "Reachable" is a
        position-only IK solve (R6), the sim's answer; the Mock's sphere test is
        that backend's own oracle.
        """
        if requested is not None:
            self._require_free_gripper(requested)
            if self._position_ik(requested, target) is None:
                raise _SkillRefused(
                    FailureCode.OUT_OF_REACH,
                    self._unreachable_reason(requested, target, action))
            return requested

        free = tuple(
            side for side in SIDE_ORDER if self._held_object[side] is None)
        if not free:
            self._refuse_both_grippers_occupied()
        for side in free:
            if self._position_ik(side, target) is not None:
                return side
        # No free gripper can reach: report the preferred one.
        raise _SkillRefused(
            FailureCode.OUT_OF_REACH,
            self._unreachable_reason(free[0], target, action))

    def _resolve_holding_side(self, requested: Side | None) -> Side:
        """Pick which gripper releases (Mock's NON-reach-aware logic, R3).

        Deliberately *not* reach-aware, exactly as the Mock: with both hands
        full, geometry must not silently decide *which object gets put down*.
        """
        if requested is not None:
            if self._held_object[requested] is None:
                raise _SkillRefused(
                    FailureCode.GRIPPER_EMPTY,
                    f'the {requested.value} gripper is empty, '
                    'there is nothing to place',
                )
            return requested
        for side in SIDE_ORDER:
            if self._held_object[side] is not None:
                return side
        raise _SkillRefused(
            FailureCode.GRIPPER_EMPTY,
            'no gripper is holding an object, there is nothing to place',
        )

    def _require_free_gripper(self, side: Side) -> None:
        """Refuse if the named gripper is already holding something (R3)."""
        held = self._held_object[side]
        if held is not None:
            raise _SkillRefused(
                FailureCode.GRIPPER_OCCUPIED,
                f'the {side.value} gripper already holds {held!r}',
            )

    def _refuse_both_grippers_occupied(self) -> NoReturn:
        """Refuse a grasp because there is no free gripper at all (R3)."""
        holdings = ', '.join(
            f'{side.value} holds {self._held_object[side]!r}'
            for side in SIDE_ORDER
        )
        raise _SkillRefused(
            FailureCode.GRIPPER_OCCUPIED, f'both grippers are occupied ({holdings})')

    def _object_ids(self) -> tuple[str, ...]:
        """Return every registered object id, for a failure reason's list (R3)."""
        return tuple(item.object_id for item in self._document.objects)

    def _find_object(self, object_id: str):
        """Return the document object named ``object_id``, or ``None`` (R3)."""
        return self._document.find_object(object_id)

    def _holder_of(self, object_id: str) -> Side | None:
        """Return the side whose gripper holds ``object_id``, or ``None`` (R4)."""
        for side in SIDE_ORDER:
            if self._held_object[side] == object_id:
                return side
        return None

    def _position_ik(self, side: Side, target: Point) -> np.ndarray | None:
        """Return the arm joints placing ``side``'s gripper at ``target`` (R6).

        The shared reach oracle for grasp/place: a **position-only** IK solve
        (orientation-free, like the Mock's distance test).  Snapshots the arm
        and lets ``solve_ik``'s ``restore`` put ``mjData`` back, so a refused
        query leaves the sim untouched -- the caller writes the result itself
        only on success.
        """
        position = np.array([target.x, target.y, target.z])
        lower = np.array([rng[0] for rng in self._arm_ranges[side]])
        upper = np.array([rng[1] for rng in self._arm_ranges[side]])
        self._snapshot_arm(side)
        return solve_ik(
            position,
            np.eye(3),
            fk=lambda q: self._arm_fk(side, q),
            joint_adrs=self._arm_joint_adrs(side),
            lower=lower,
            upper=upper,
            restore=lambda: self._restore_arm(side),
            position_only=True,
        )

    def _write_arm(self, side: Side, solved: np.ndarray) -> None:
        """Write an arm's five qpos slots and position actuators, then forward.

        Servo hold (R4), exactly as :meth:`_move_gripper` does on success.
        """
        data = self._data
        for value, (qpos_adr, ctrl_id) in zip(solved, self._arm_joints[side].values()):
            data.qpos[qpos_adr] = float(value)
            data.ctrl[ctrl_id] = float(value)
        mujoco.mj_forward(self._model, data)

    # -- carried-object book-keeping (PR4, R2) ------------------------------

    def _carry_held_objects(self) -> None:
        """Keep every held object glued to the gripper holding it (R2).

        Mirrors ``mock_backend._carry_held_objects``: after *every* skill that
        can move the load, re-write the held object's free-joint qpos to
        ``gripper_frame ∘ offset`` and ``mj_forward``.  No ``mj_step``.
        """
        for side in SIDE_ORDER:
            held_id = self._held_object[side]
            offset = self._held_offset[side]
            if held_id is None or offset is None:
                continue
            world_pose = _compose(self._gripper_pose(side), offset)
            self._set_object_pose(held_id, world_pose)
        mujoco.mj_forward(self._model, self._data)

    def _set_object_pose(self, object_id: str, pose: Pose) -> None:
        """Write an object's free-joint qpos from ``pose`` (position + w-first quat).

        The same convention :meth:`_set_base_pose` uses for the base free joint
        (MuJoCo free-joint qpos is x, y, z, qw, qx, qy, qz).
        """
        adr = self._object_free_qpos[object_id]
        p, q = pose.position, pose.orientation
        data = self._data
        data.qpos[adr + 0] = p.x
        data.qpos[adr + 1] = p.y
        data.qpos[adr + 2] = p.z
        data.qpos[adr + 3] = q.w
        data.qpos[adr + 4] = q.x
        data.qpos[adr + 5] = q.y
        data.qpos[adr + 6] = q.z

    # -- arm + gripper mechanics (PR3) -------------------------------------

    def _shoulder(self, side: Side) -> Point:
        """Return the world-frame shoulder point of one arm (Mock's model).

        Reuses :class:`~robot_backends.mock_world.RobotModel` -- the shared
        body-constants holder that reads the URDF -- so the refusal reason
        below is semantically identical to the Mock's (R3).  The model ignores
        base orientation, exactly as the Mock does.
        """
        return self._robot.shoulder(
            self._base_pose(),
            float(self._data.qpos[self._column_qpos_adr]),
            side)

    def _unreachable_reason(self, side: Side, target: Point, action: str) -> str:
        """Return the Mock's exact ``OUT_OF_REACH`` reason text for ``target``."""
        distance = target.distance_to(self._shoulder(side))
        reach = self._robot.reach_radius
        return (
            f'cannot {action}: it is {distance:.2f} m from the {side.value} '
            f'shoulder, beyond the {reach:.2f} m reach '
            f'(robot is at {self._location!r})')

    def _arm_fk(
        self, side: Side, q: np.ndarray,
    ) -> tuple[np.ndarray, np.ndarray]:
        """Set an arm's five qpos slots, forward, and return its task frame.

        The IK callback (R1): it touches **only** the five arm joints -- never
        the base, column or jaws -- and reports the jaw-midpoint position plus
        the upper jaw's rotation matrix, the same frame :meth:`_gripper_frame`
        reports (R2).
        """
        data = self._data
        for value, (qpos_adr, _) in zip(q, self._arm_joints[side].values()):
            data.qpos[qpos_adr] = float(value)
        mujoco.mj_forward(self._model, data)
        return self._task_frame(side)

    def _arm_joint_adrs(self, side: Side) -> list[int]:
        """Return an arm's five qpos slots in joint order."""
        return [adr for adr, _ in self._arm_joints[side].values()]

    def _task_frame(self, side: Side) -> tuple[np.ndarray, np.ndarray]:
        """Return ``(jaw-midpoint, upper-jaw rotation matrix)`` from mjData.

        The single source of the IK task frame (R2): both :meth:`_arm_fk` and
        :meth:`_gripper_frame` read it, so the commanded target and the
        reported observation cannot disagree.
        """
        data = self._data
        upper = data.xpos[self._jaw_upper[side]]
        lower = data.xpos[self._jaw_lower[side]]
        midpoint = 0.5 * (np.asarray(upper) + np.asarray(lower))
        quat = data.xquat[self._jaw_upper[side]]  # (w, x, y, z)
        return midpoint, _wxyz_to_matrix(quat)

    def _snapshot_arm(self, side: Side) -> None:
        """Record an arm's five qpos values so a failed solve can restore them."""
        self._arm_snapshot[side] = np.array(
            [self._data.qpos[adr] for adr in self._arm_joint_adrs(side)])

    def _restore_arm(self, side: Side) -> None:
        """Put an arm's five qpos slots back and forward (the solve's exit path).

        ``solve_ik`` calls this on every exit, so a refused solve leaves
        ``mjData`` exactly as it found it (R1/R3 "world unchanged on refusal").
        """
        snapshot = self._arm_snapshot[side]
        for value, qpos_adr in zip(snapshot, self._arm_joint_adrs(side)):
            self._data.qpos[qpos_adr] = float(value)
        mujoco.mj_forward(self._model, self._data)

    def _gripper_target(
        self, side: Side, *, closed: bool,
    ) -> tuple[dict[str, float], dict[str, float]]:
        """Return the ``(qpos, ctrl)`` targets for a gripper's jaws (R5/R6).

        The mirror is commanded to the negated driven value: MuJoCo drops the
        URDF's ``<mimic>``, so the two joints move independently and the
        symmetric posture must be written out explicitly (R5).  ``open`` uses
        the driven joint's lower limit (its URDF open angle) with the mirror at
        the negated value; ``closed`` is zero on both.
        """
        lower, _ = self._gripper_ranges[side][0]
        if closed:
            return {'driven': 0.0, 'mirror': 0.0}, {'driven': 0.0, 'mirror': 0.0}
        return (
            {'driven': lower, 'mirror': -lower},
            {'driven': lower, 'mirror': -lower},
        )

    def _set_gripper(self, side: Side, *, closed: bool) -> None:
        """Write a gripper's jaw qpos and servo targets, then forward (R4/R5)."""
        qpos, ctrl = self._gripper_target(side, closed=closed)
        data = self._data
        for key in ('driven', 'mirror'):
            qpos_adr, ctrl_id = self._gripper_joints[side][key]
            data.qpos[qpos_adr] = qpos[key]
            data.ctrl[ctrl_id] = ctrl[key]
        mujoco.mj_forward(self._model, data)

    def _gripper_state(self, side: Side) -> GripperState:
        """Return a gripper's OPEN/CLOSED state from the driven jaw's qpos (R6).

        Derived (never hardcoded) so an open/close skill is observable: OPEN at
        or below the midpoint of the driven joint's travel, CLOSED above it.
        """
        lower, upper = self._gripper_ranges[side][0]
        driven_adr = self._gripper_joints[side]['driven'][0]
        qpos = float(self._data.qpos[driven_adr])
        return GripperState.OPEN if qpos <= (lower + upper) / 2.0 else GripperState.CLOSED

    # -- observation helpers ------------------------------------------------

    def _base_pose(self) -> Pose:
        """Return the base's world-frame pose, read from the sim.

        Reads the free-jointed ``base_link`` body's ``xpos``/``xquat`` directly
        from ``mjData`` (R5) -- the world frame -- rather than pinning the base
        to the document's start-location pose.  World frame == map frame (R-2),
        so the numbers travel across unmodified; the base can now actually be
        somewhere else after a :meth:`_navigate_to`, and this reports where it
        really is.
        """
        return _world_from_xpos(self._data, self._base_body)

    def _object_pose(self, object_id: str) -> Pose:
        """Return the reported (world/map) pose of a welded scene object."""
        return _world_from_xpos(self._data, self._object_body[object_id])

    def _gripper_frame(self, side: Side) -> tuple[Point, Quaternion]:
        """Return the reported gripper frame: jaw midpoint and upper-jaw quat (Q3).

        Position is the midpoint of the two jaws -- the natural grasp centre of
        the gripper.  Orientation is the upper jaw body's.  This is the *one*
        place that frame is derived (:meth:`_task_frame` is the numeric
        twin), so IK's target and the observation agree by construction (R2).
        """
        midpoint, _ = self._task_frame(side)
        quat = self._data.xquat[self._jaw_upper[side]]  # (w, x, y, z)
        return (
            Point(float(midpoint[0]), float(midpoint[1]), float(midpoint[2])),
            Quaternion(
                x=float(quat[1]), y=float(quat[2]),
                z=float(quat[3]), w=float(quat[0])),
        )

    def _gripper_pose(self, side: Side) -> Pose:
        """Return the world-frame pose of one gripper."""
        position, orientation = self._gripper_frame(side)
        return Pose(position=position, orientation=orientation)

    def _gripper_observation(self, side: Side) -> GripperObservation:
        """Return the reportable state of one gripper (state + held load, R4).

        ``held_object_id`` is the object the book-keeping says this gripper
        holds (or ``None``), and ``grasped`` is exactly ``held_object_id is not
        None`` -- the Mock's derivation (D19): a jaw posture with no load grips
        nothing, and that is a fact the observation reports rather than an
        error.
        """
        held_id = self._held_object[side]
        return GripperObservation(
            side=side,
            state=self._gripper_state(side),
            pose=self._gripper_pose(side),
            held_object_id=held_id,
            grasped=held_id is not None,
        )


def _compose(gripper_pose: Pose, offset: Pose) -> Pose:
    """Return the world pose of ``offset`` (object in gripper frame) under ``gripper_pose``.

    ``world_R = R_gripper @ R_offset`` and ``world_p = p_gripper +
    R_gripper @ p_offset``.
    """
    rot_g = _quat_to_matrix(gripper_pose.orientation)
    rot_o = _quat_to_matrix(offset.orientation)
    position = np.asarray([
        gripper_pose.position.x, gripper_pose.position.y, gripper_pose.position.z])
    offset_position = np.asarray([
        offset.position.x, offset.position.y, offset.position.z])
    world_position = position + rot_g @ offset_position
    world_rotation = rot_g @ rot_o
    return Pose(
        position=Point(
            float(world_position[0]), float(world_position[1]),
            float(world_position[2])),
        orientation=_matrix_to_quat(world_rotation),
    )


def _offset_between(gripper_pose: Pose, object_pose: Pose) -> Pose:
    """Return ``object_pose`` expressed in the gripper frame (the grasp offset).

    The inverse of :func:`_compose`: ``offset_R = R_gripper.T @ R_object`` and
    ``offset_p = R_gripper.T @ (p_object - p_gripper)``.
    """
    rot_g = _quat_to_matrix(gripper_pose.orientation)
    rot_o = _quat_to_matrix(object_pose.orientation)
    gripper_position = np.asarray([
        gripper_pose.position.x, gripper_pose.position.y, gripper_pose.position.z])
    object_position = np.asarray([
        object_pose.position.x, object_pose.position.y, object_pose.position.z])
    offset_position = rot_g.T @ (object_position - gripper_position)
    offset_rotation = rot_g.T @ rot_o
    return Pose(
        position=Point(
            float(offset_position[0]), float(offset_position[1]),
            float(offset_position[2])),
        orientation=_matrix_to_quat(offset_rotation),
    )


def _matrix_to_quat(rotation: np.ndarray) -> Quaternion:
    """Return a skill-API ``Quaternion`` (x, y, z, w) from a 3x3 rotation matrix.

    Standard Shepperd/Branch-free conversion: pick the largest diagonal term to
    stay numerically stable near 180-degree rotations, then normalise.
    """
    m = np.asarray(rotation, dtype=float)
    trace = float(m[0, 0] + m[1, 1] + m[2, 2])
    if trace > 0.0:
        s = 0.5 / np.sqrt(trace + 1.0)
        w = 0.25 / s
        x = (m[2, 1] - m[1, 2]) * s
        y = (m[0, 2] - m[2, 0]) * s
        z = (m[1, 0] - m[0, 1]) * s
    elif m[0, 0] > m[1, 1] and m[0, 0] > m[2, 2]:
        s = 2.0 * np.sqrt(1.0 + m[0, 0] - m[1, 1] - m[2, 2])
        w = (m[2, 1] - m[1, 2]) / s
        x = 0.25 * s
        y = (m[0, 1] + m[1, 0]) / s
        z = (m[0, 2] + m[2, 0]) / s
    elif m[1, 1] > m[2, 2]:
        s = 2.0 * np.sqrt(1.0 + m[1, 1] - m[0, 0] - m[2, 2])
        w = (m[0, 2] - m[2, 0]) / s
        x = (m[0, 1] + m[1, 0]) / s
        y = 0.25 * s
        z = (m[1, 2] + m[2, 1]) / s
    else:
        s = 2.0 * np.sqrt(1.0 + m[2, 2] - m[0, 0] - m[1, 1])
        w = (m[1, 0] - m[0, 1]) / s
        x = (m[0, 2] + m[2, 0]) / s
        y = (m[1, 2] + m[2, 1]) / s
        z = 0.25 * s
    quat = np.array([x, y, z, w], dtype=float)
    norm = float(np.linalg.norm(quat))
    if norm > 0.0:
        quat = quat / norm
    return Quaternion(
        x=float(quat[0]), y=float(quat[1]), z=float(quat[2]), w=float(quat[3]))


def _quat_to_matrix(quaternion: Quaternion) -> np.ndarray:
    """Return the 3x3 rotation matrix of a skill-API ``Quaternion`` (x, y, z, w)."""
    q = np.array([quaternion.x, quaternion.y, quaternion.z, quaternion.w], dtype=float)
    norm = float(np.linalg.norm(q))
    if norm > 0.0:
        q = q / norm
    x, y, z, w = q
    return np.array([
        [1 - 2 * (y * y + z * z), 2 * (x * y - z * w), 2 * (x * z + y * w)],
        [2 * (x * y + z * w), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w)],
        [2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y)],
    ])


def _wxyz_to_matrix(quaternion: np.ndarray) -> np.ndarray:
    """Return the 3x3 rotation matrix of a MuJoCo ``xquat`` (w, x, y, z)."""
    w, x, y, z = (float(v) for v in quaternion)
    return np.array([
        [1 - 2 * (y * y + z * z), 2 * (x * y - z * w), 2 * (x * z + y * w)],
        [2 * (x * y + z * w), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w)],
        [2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y)],
    ])


def _world_from_xpos(data: mujoco.MjData, body_id: int) -> Pose:
    """Build a :class:`Pose` from an mjData body's ``xpos``/``xquat``.

    MuJoCo stores orientation as (w, x, y, z); the skill API's
    :class:`Quaternion` stores (x, y, z, w).  World frame == map frame (R-2),
    so the numbers travel across unmodified.
    """
    pos = data.xpos[body_id]
    quat = data.xquat[body_id]
    return Pose(
        position=Point(
            float(pos[0]), float(pos[1]), float(pos[2])),
        orientation=Quaternion(
            x=float(quat[1]), y=float(quat[2]),
            z=float(quat[3]), w=float(quat[0])),
    )
