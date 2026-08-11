# Copyright (c) 2026 Jaime C.
#
# Use of this source code is governed by an MIT-style
# license that can be found in the LICENSE file or at
# https://opensource.org/licenses/MIT.

"""The collision-guard seam, plus stub geometry crude enough to be honest.

Real collision checking needs a robot model, meshes and swept volumes -- a
later feature, and one that will want MoveIt rather than anything written here
(CLAUDE.md invariant 5: reuse, don't reinvent).  What this module fixes now is
the *shape* of the seam, so that landing real geometry is an implementation of
an existing protocol rather than a redesign of the layer.

:class:`CollisionGuard` is a :class:`typing.Protocol`, not an ABC: a MoveIt- or
MuJoCo-backed checker will already be somebody else's class, and demanding it
inherit from us to be usable would be the wrong tax.

Guards **abort**; they never rewrite.  Clamping is only sound for a scalar
where "less of it" is strictly safer (the column height).  A 6-DoF goal has no
such ordering -- nudging a ``Place`` out of a keep-out region would put the mug
down somewhere the brain did not ask for, which is worse than refusing.
"""

from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from robot_safety.events import SafetyEvent, SafetyEventKind
from robot_safety.limits import KeepOutBox, SafetyConfigError, SafetyLimits
from robot_safety.policy import policy_for
from robot_safety.state import SafetyState
from robot_skills import Pose, Skill

__all__ = ['CollisionGuard', 'KeepOutBoxGuard', 'NullCollisionGuard', 'target_pose']


def target_pose(skill: Skill) -> Pose | None:
    """Return the Cartesian target a skill commands, or ``None`` if it has none.

    Which skills those are is read from :data:`~robot_safety.policy.SKILL_POLICIES`
    rather than from an ``isinstance`` chain here, so a skill added upstream
    cannot acquire "no geometry to check" by default: it has no policy at all,
    and the gate refuses it before any guard is asked.
    """
    policy = policy_for(skill)
    if policy is None or not policy.has_cartesian_target:
        return None
    return skill.pose


@runtime_checkable
class CollisionGuard(Protocol):
    """Anything that can veto a skill on geometric grounds.

    Two obligations on an implementor, both enforced by the layer:

    1. **Abort, never rewrite.**  Return an event with no ``clamped_value``.
       Nudging a 6-DoF goal out of the way would put the object somewhere
       nobody asked for, which is worse than refusing; a returned clamp record
       raises ``ValueError`` out of ``SafetyLayer.filter``.  Returning anything
       that is not a :class:`SafetyEvent` or ``None`` raises ``TypeError``.
    2. **Be total.**  ``check`` is called on *every* skill the gate accepts so
       far, including ones with no Cartesian target, and it must answer rather
       than raise.  An exception is not caught: it propagates out of ``filter``
       and no motion follows.  That is the right failure -- a guard that
       crashed checked nothing -- but it is an outage, not a refusal, so a
       guard that cannot decide should return ``None`` (clear) or an event
       (abort) on purpose rather than by accident.
    """

    def check(self, skill: Skill, state: SafetyState) -> SafetyEvent | None:
        """Return a :class:`SafetyEvent` to abort the skill, or ``None`` if clear."""


@dataclass(frozen=True)
class NullCollisionGuard:
    """The permissive default: every skill is geometrically clear.

    The honest default for a package that has no robot model yet.  It is a
    named class rather than an ``if guard is None`` branch so that "nothing is
    checking geometry here" is visible in a caller's construction site.
    """

    def check(self, skill: Skill, state: SafetyState) -> SafetyEvent | None:
        """Return ``None``: this guard never objects."""
        return None


@dataclass(frozen=True)
class KeepOutBoxGuard:
    """Abort any skill whose target pose falls inside a configured region.

    Stub geometry, and deliberately so: it knows nothing about the robot's
    body, its path or its swept volume, only about where the *goal* is.  That
    is still enough to stop a target below the floor or inside the stove, and
    it makes the seam a working part rather than a declared one.
    """

    boxes: tuple[KeepOutBox, ...] = ()

    def __post_init__(self) -> None:
        boxes = tuple(self.boxes)
        for box in boxes:
            if not isinstance(box, KeepOutBox):
                raise TypeError(
                    'KeepOutBoxGuard.boxes must contain KeepOutBox values, '
                    f'got {type(box).__name__}')
        object.__setattr__(self, 'boxes', boxes)

    @classmethod
    def from_limits(cls, limits: SafetyLimits) -> 'KeepOutBoxGuard':
        """Build a guard from the ``keep_out_boxes`` section of a limit set.

        Refuses a limit set with no regions configured: a guard built from
        config that vetoes nothing is the dead parameter this seam exists to
        avoid, reached by configuration instead of by design.  Wiring one in
        and getting silence is worse than not wiring one in at all.  An
        intentionally empty guard stays expressible as ``KeepOutBoxGuard(())``.
        """
        if not limits.keep_out_boxes:
            raise SafetyConfigError(
                'keep_out_boxes: no regions configured, so KeepOutBoxGuard would check '
                'nothing; configure a region or use NullCollisionGuard deliberately')
        return cls(boxes=limits.keep_out_boxes)

    def check(self, skill: Skill, state: SafetyState) -> SafetyEvent | None:
        """Return a ``COLLISION_RISK`` event if the target is inside a region."""
        pose = target_pose(skill)
        if pose is None:
            return None
        position = pose.position
        for box in self.boxes:
            if box.contains(position):
                return SafetyEvent(
                    kind=SafetyEventKind.COLLISION_RISK,
                    detail=(
                        f'{skill.name} target ({position.x:g}, {position.y:g}, '
                        f'{position.z:g}) m lies inside keep-out region {box.label!r}'),
                    side=getattr(skill, 'side', None),
                )
        return None
