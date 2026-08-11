# Copyright (c) 2026 Jaime C.
#
# Use of this source code is governed by an MIT-style
# license that can be found in the LICENSE file or at
# https://opensource.org/licenses/MIT.

"""The gate itself: ``filter(skill, state) -> ClampedCall | SafetyEvent``.

**The layer is pure.**  It keeps configuration and a collision guard, and
nothing else: no memory of previous calls, no mutation, so the same
``(skill, state)`` always produces the same verdict.  That is what lets one
gate serve two execution models.  Today every backend is synchronous
(``RobotBackend.execute`` returns when the motion is over, so there is no
in-flight command to abort), and the layer is called once, before execution.
When an asynchronous backend arrives, "clamp or abort in flight" (D17) is the
same function re-asked against successive telemetry samples -- not a redesign.

**Check order is fixed, and aborts precede clamps** (all five checks, in
order): e-stop, collision, measured velocity, gripper over-force, then the
column clamp.  E-stop first so an e-stopped over-limit call reports the e-stop
rather than a clamp -- the most urgent true statement wins.  Rewriting a call
that is about to be refused would be wasted work and a confusing record.

**What is clamped, and what is not.**  Exactly one field is rewritten:
``ExtendColumn.height``, the one scalar in the skill API where "less of it" is
unambiguously safer and which the skill types deliberately leave unclamped for
this layer.  Cartesian poses are never clamped: an out-of-range *pose* is
reachability, which D17 assigns to the backend's up-front refusal, and a 6-DoF
goal has no safe direction to be nudged in.  Geometry is aborted by the
collision guard, never rewritten.
"""

from dataclasses import dataclass

from robot_safety.collision import CollisionGuard, NullCollisionGuard
from robot_safety.events import SafetyEvent, SafetyEventKind
from robot_safety.limits import MotionLimits, SafetyLimits
from robot_safety.state import MotionAxis, SafetyState
from robot_skills import CloseGripper, ExtendColumn, Grasp, Side, SIDE_ORDER, Skill

__all__ = ['ClampedCall', 'SafetyLayer']


@dataclass(frozen=True)
class ClampedCall:
    """An accepted call: the skill to execute, the envelope, what was rewritten.

    ``skill`` **is the caller's own object** when nothing was clamped -- the
    same instance, not an equal rebuild -- so "passed through unchanged" is
    checkable with ``is`` rather than believed.

    ``limits`` travels with the call because the check the layer *cannot*
    perform is the one that matters most during motion: only the code below
    this seam can actually rate-limit a trajectory.  The layer says how fast the
    machine may move; the backend is contractually required to honour it.

    ``clamps`` records every rewrite, so a caller can log what safety changed
    about a command it issued without diffing two skills.
    """

    skill: Skill
    limits: MotionLimits
    clamps: tuple[SafetyEvent, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.skill, Skill):
            raise TypeError(
                f'ClampedCall.skill must be a Skill, got {type(self.skill).__name__}')
        if not isinstance(self.limits, MotionLimits):
            raise TypeError(
                'ClampedCall.limits must be a MotionLimits, '
                f'got {type(self.limits).__name__}')
        clamps = tuple(self.clamps)
        for event in clamps:
            if not isinstance(event, SafetyEvent):
                raise TypeError(
                    'ClampedCall.clamps must contain SafetyEvent values, '
                    f'got {type(event).__name__}')
            if not event.is_clamp:
                raise ValueError(
                    f'ClampedCall.clamps got an abort event ({event.kind.value}); '
                    'an event with no clamped value is returned instead of a call')
        object.__setattr__(self, 'clamps', clamps)

    @property
    def was_clamped(self) -> bool:
        """Return whether the safety layer rewrote anything."""
        return bool(self.clamps)


def _closing_sides(skill: Skill) -> tuple[Side, ...]:
    """Return the gripper sides a skill closes jaws on (empty for the rest).

    D19 draws the line at *closing*: over-force is a safety event on the clamp
    path, while closing on nothing is an ordinary success.  ``Grasp`` with no
    side is checked on **both** grippers -- the backend picks one and we cannot
    know which, so the conservative reading is the only sound one.

    ``OpenGripper`` is deliberately absent, and that absence is a safety
    property, not an oversight: opening is the *remedy* for over-force, so
    gating it on force would make an over-force state unrecoverable -- the
    robot could never let go.
    """
    if not isinstance(skill, (CloseGripper, Grasp)):
        return ()
    if skill.side is None:
        return SIDE_ORDER
    return (skill.side,)


class SafetyLayer:
    """The clamp/abort gate between brain-issued skills and a backend (D4/D17).

    Backend-agnostic on purpose: Mock, Sim and Real get the same limits, and
    each backend keeps only its own reachability refusals.

    Example::

        layer = SafetyLayer()                     # defaults from limits.yaml
        verdict = layer.filter(skill, state)
        if isinstance(verdict, SafetyEvent):
            report(verdict)                       # aborted; nothing executed
        else:
            backend.execute(verdict.skill)        # possibly clamped
    """

    def __init__(
        self,
        limits: SafetyLimits | None = None,
        collision_guard: CollisionGuard | None = None,
    ) -> None:
        """Build a layer from a limit set and a collision guard.

        Both default conservatively-but-honestly: the shipped ``limits.yaml``,
        and a null guard that checks no geometry, since this package has no
        robot model yet.
        """
        if limits is None:
            limits = SafetyLimits.defaults()
        if not isinstance(limits, SafetyLimits):
            raise TypeError(
                f'SafetyLayer.limits must be a SafetyLimits, got {type(limits).__name__}')
        if collision_guard is None:
            collision_guard = NullCollisionGuard()
        if not callable(getattr(collision_guard, 'check', None)):
            raise TypeError(
                'SafetyLayer.collision_guard must implement check(skill, state); '
                f'{type(collision_guard).__name__} does not')
        self._limits = limits
        self._collision_guard = collision_guard

    @property
    def limits(self) -> SafetyLimits:
        """Return the limit set this layer enforces."""
        return self._limits

    @property
    def collision_guard(self) -> CollisionGuard:
        """Return the injected collision guard."""
        return self._collision_guard

    # ``filter`` shadows the builtin, which flake8-builtins flags; the name is
    # the layer's published contract (issue #43, D17), so it is kept and the
    # one line is silenced rather than renaming the seam around a linter.
    def filter(self, skill: Skill, state: SafetyState) -> ClampedCall | SafetyEvent:  # noqa: A003
        """Return the call to execute, or the safety event that stopped it.

        A :class:`SafetyEvent` return means **abort**: the skill must not be
        executed.  A :class:`ClampedCall` means execute ``call.skill`` --
        which is the very object passed in unless ``call.was_clamped``.

        Raises ``TypeError`` for arguments of the wrong type: that is a
        programming error in the caller, not an unsafe robot, and silently
        turning it into a safety event would hide it.
        """
        if not isinstance(skill, Skill):
            raise TypeError(f'skill must be a Skill, got {type(skill).__name__}')
        if not isinstance(state, SafetyState):
            raise TypeError(f'state must be a SafetyState, got {type(state).__name__}')

        for check in (
            self._check_estop,
            self._check_collision,
            self._check_velocities,
            self._check_gripper_force,
        ):
            event = check(skill, state)
            if event is not None:
                return event

        skill, clamps = self._clamp(skill)
        return ClampedCall(skill=skill, limits=self._limits.motion, clamps=clamps)

    def _check_estop(self, skill: Skill, state: SafetyState) -> SafetyEvent | None:
        """Abort everything while the e-stop is engaged."""
        if not state.estop_engaged:
            return None
        return SafetyEvent(
            kind=SafetyEventKind.ESTOP_ENGAGED,
            detail=f'e-stop engaged; {skill.name} refused, all motion is inhibited',
        )

    def _check_collision(self, skill: Skill, state: SafetyState) -> SafetyEvent | None:
        """Ask the injected guard, and insist on the protocol's return type."""
        event = self._collision_guard.check(skill, state)
        if event is None or isinstance(event, SafetyEvent):
            return event
        raise TypeError(
            f'{type(self._collision_guard).__name__}.check must return a SafetyEvent '
            f'or None, got {type(event).__name__}')

    def _check_velocities(self, skill: Skill, state: SafetyState) -> SafetyEvent | None:
        """Abort while *any* axis is over its cap, related to this skill or not.

        A base running at 2 m/s makes commanding an arm motion unsafe just the
        same: the whole machine is in an unsafe dynamic state, and a
        skill-to-axis map would be more code buying less safety.  Axes are
        visited in enum order so the reported axis is deterministic.
        """
        for axis in MotionAxis:
            speed = state.velocity(axis)
            if speed is None:
                continue
            cap = self._limits.motion.velocity_cap(axis)
            if speed > cap:
                return SafetyEvent(
                    kind=SafetyEventKind.VELOCITY_EXCEEDED,
                    detail=(
                        f'measured {axis.value} speed {speed:g} m/s exceeds the '
                        f'{cap:g} m/s cap; {skill.name} refused'),
                    offending_value=speed,
                    limit=cap,
                    axis=axis,
                )
        return None

    def _check_gripper_force(self, skill: Skill, state: SafetyState) -> SafetyEvent | None:
        """Abort a jaw-closing skill while that gripper is already over-force."""
        cap = self._limits.motion.max_gripper_force
        for side in _closing_sides(skill):
            force = state.gripper_force(side)
            if force is None or force <= cap:
                continue
            return SafetyEvent(
                kind=SafetyEventKind.GRIPPER_OVERFORCE,
                detail=(
                    f'measured {side.value} gripper force {force:g} N exceeds the '
                    f'{cap:g} N limit; {skill.name} would close harder'),
                offending_value=force,
                limit=cap,
                side=side,
            )
        return None

    def _clamp(self, skill: Skill) -> tuple[Skill, tuple[SafetyEvent, ...]]:
        """Return the skill to execute plus a record of every rewrite.

        Returns the caller's own object when nothing needed changing, so an
        in-limit call passes through identically and not merely equally.
        """
        if not isinstance(skill, ExtendColumn):
            return skill, ()
        column = self._limits.column
        bound = column.violated_bound(skill.height)
        if bound is None:
            return skill, ()
        clamped = column.clamp(skill.height)
        event = SafetyEvent(
            kind=SafetyEventKind.COLUMN_LIMIT,
            detail=(
                f'commanded column height {skill.height:g} m is outside the '
                f'[{column.min_height:g}, {column.max_height:g}] m travel range; '
                f'clamped to {clamped:g} m'),
            offending_value=skill.height,
            limit=bound,
            clamped_value=clamped,
        )
        return ExtendColumn(height=clamped), (event,)
