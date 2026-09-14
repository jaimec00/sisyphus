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

**Static world objects (R-1).**  A compiled ``MjModel`` cannot gain bodies
post-compile, so the scene-aware build welds each world object into the merged
MJCF as a **static, joint-less body** *before* compilation -- reusing the PR7
merge machinery via :func:`load_mjcf_model_with_scene` (see that function's
docstring for the seam).  Static bodies never receive dynamics -- no gravity
collapse, no drift -- so an object's world frame equals its seed pose exactly,
across any number of ``mj_step`` calls.  See ``implementation.md`` for the PR5
hand-off.

**Frame mapping (R-2).**  At the seed, the base sits at the world origin, which
IS the seed map's ``start_location`` (``charger`` at (0,0,0)); MJCF +z == map
+z.  So the map frame and the MuJoCo world frame are the same frame and the
overlay is the identity: an object's seed ``Pose.position`` maps directly onto
its ``<body pos=...>``, and :meth:`get_observation` reports the body's
``xpos``/``xquat`` verbatim as the map coordinate.  Once the base free joint
moves it (PR2 ``navigate_to``), the world frame still *is* the map frame -- the
base's reported pose is just no longer the origin.

**Posture on reset (R-3).**  ``reset`` homes the robot: wheels at velocity 0,
``column_lift`` clamped to the seed ``start_column_height`` with its position
actuator commanded to the same value so the servo *holds* it, arms/grippers at
joint zero.  ``column_height`` is read back from the ``column_lift`` qpos
slot, so it always reports what the simulated lift actually is.

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
(robot_mcp) owns clamping.  Every *other* legal skill keeps PR1's refusal --
``status=failed`` with :class:`~robot_skills.FailureCode.UNSUPPORTED_SKILL` and
an unchanged world (the ABC's total contract; see :meth:`execute`).  Grasp/place
/IK and arm kinematics arrive in PR3+, and the primitive scene geometry below is
a placeholder for roadmap #4's visuals.
"""

from __future__ import annotations

from typing import Mapping

import mujoco

from robot_backends.interface import RobotBackend
from robot_description.mjcf_model import load_mjcf_model_with_scene
from robot_skills import (
    ExtendColumn,
    FailureCode,
    GripperObservation,
    GripperState,
    NavigateTo,
    Observation,
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


def _world_bodies_xml(document: WorldDocument) -> str:
    """Render ``document.objects`` as static ``<body>`` blocks for the worldbody.

    Each object becomes one joint-less, welded body named by its ``object_id``
    (seed ids are alphanumeric+underscore, valid MuJoCo names) and placed at
    its seed pose.  Its label selects a primitive geom via :func:`_geom_attrs`;
    ``graspable`` is deliberately *not* modelled in PR1 (static bodies, no
    joints, contact off) -- it survives only as the flag reported in the
    Observation from the source document, and PR5's grasp work replaces these
    bodies with free joints resting on a modelled floor/surface.
    """
    blocks = []
    for item in document.objects:
        geom_type, size, rgba = _geom_attrs(item.label)
        p = item.pose.position
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

    Every other skill keeps PR1's refusal: :meth:`execute` returns
    ``status=failed`` with
    :class:`~robot_skills.FailureCode.UNSUPPORTED_SKILL`, leaving the world
    unchanged -- exactly what the ABC's *total* contract asks for ("a skill
    that cannot be carried out returns ``status=failed`` ... and leaves the
    world state unchanged").  ``navigate_to``/``extend_column`` never raise for
    a legal :class:`Skill` either; only a non-``Skill`` raises ``TypeError``.
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

        # Arms + grippers + mirrors are returned to joint zero (their position
        # actuator's servo target) on reset.  The free joint is /excluded/ from
        # the joint sweep (R5): it is homed to the start-location pose instead.
        self._home_joints = self._joint_ids_excluding(
            _WHEEL_JOINTS + (_COLUMN_JOINT, _BASE_FREE))
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

        # Gripper jaw body ids, keyed by Side.
        self._jaw_upper: dict[Side, int] = {}
        self._jaw_lower: dict[Side, int] = {}
        for side in SIDE_ORDER:
            body_id = self._body_id(f'{side.value}_{_JAW_UPPER}')
            self._jaw_upper[side] = body_id
            self._jaw_lower[side] = self._body_id(f'{side.value}_{_JAW_LOWER}')

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

        # Arms + grippers + mirrors: home at joint zero (position actuators --
        # a ctrl of 0 makes the zero joint the servo's target).  The free joint
        # is excluded from this sweep (R5) -- it was homed above instead.
        for jid in self._home_joints:
            data.qpos[self._model.jnt_qposadr[jid]] = 0.0
        for aid in self._home_position_actuators:
            data.ctrl[aid] = 0.0

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
                    held_by=None,
                )
                for item in sorted(
                    self._document.objects, key=lambda o: o.object_id)
            ),
            known_locations=tuple(sorted(self._document.locations)),
        )

    def execute(self, skill: Skill) -> SkillResult:
        """Execute one skill, returning its status and a fresh observation.

        ``NavigateTo`` and ``ExtendColumn`` dispatch to their handlers (PR2);
        every other skill is a clean ``status=failed`` with
        ``UNSUPPORTED_SKILL`` and an unchanged observation.  Per the ABC's
        *total* contract this never raises for a legal :class:`Skill`, and it
        only raises for something that is not a ``Skill`` at all.
        """
        if not isinstance(skill, Skill):
            raise TypeError(
                f'execute() expects a Skill, got {type(skill).__name__}')
        try:
            if isinstance(skill, NavigateTo):
                note = self._navigate_to(skill)
            elif isinstance(skill, ExtendColumn):
                note = self._extend_column(skill)
            else:
                return SkillResult.failure(
                    skill,
                    self.get_observation(),
                    FailureCode.UNSUPPORTED_SKILL,
                    f'the MuJoCo backend does not implement skill {skill.name!r}',
                )
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

    def _gripper_pose(self, side: Side) -> Pose:
        """Return the world-frame pose of one gripper (jaw midpoint, Q3).

        Position is the midpoint of the two open jaws -- the natural grasp
        centre of an empty, open gripper.  Orientation is the upper jaw body's.
        This is a PR1 placeholder read straight from the sim; PR2's arm
        kinematics will report true commanded poses.
        """
        up = self._data.xpos[self._jaw_upper[side]]
        lo = self._data.xpos[self._jaw_lower[side]]
        mid = ((up[0] + lo[0]) * 0.5, (up[1] + lo[1]) * 0.5, (up[2] + lo[2]) * 0.5)
        quat = self._data.xquat[self._jaw_upper[side]]  # (w, x, y, z)
        return Pose(
            position=Point(mid[0], mid[1], mid[2]),
            orientation=Quaternion(
                x=float(quat[1]), y=float(quat[2]),
                z=float(quat[3]), w=float(quat[0])),
        )

    def _gripper_observation(self, side: Side) -> GripperObservation:
        """Return the reportable state of one gripper (empty/open on reset)."""
        return GripperObservation(
            side=side,
            state=GripperState.OPEN,
            pose=self._gripper_pose(side),
            held_object_id=None,
            grasped=False,
        )


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
