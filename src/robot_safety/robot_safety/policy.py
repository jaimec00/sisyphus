# Copyright (c) 2026 Jaime C.
#
# Use of this source code is governed by an MIT-style
# license that can be found in the LICENSE file or at
# https://opensource.org/licenses/MIT.

"""Which checks apply to which skill -- stated once, in one exhaustive table.

Dispatching on ``isinstance`` at each check site looks harmless and is not: the
skill vocabulary grows (D18 calls an added field non-breaking, and a new skill
is a routine feature), and every ``isinstance`` chain defaults to *permissive*.
A skill this layer has never heard of would flow through unclamped, with no
geometry check and outside the force check, while every existing test stayed
green.  "Nobody wrote a rule for it" is the one reason a safety layer may not
let something past.

So the vocabulary is enumerated exactly once, here, and the layer's answer to a
skill it cannot find is to **refuse it** (``SafetyEventKind.UNCLASSIFIED_SKILL``
-- fail closed at runtime), while :func:`unclassified_skills` makes the same
gap a loud test failure at development time.  This is the discipline the
package already applies to its other vocabulary: a :class:`MotionAxis` with no
configured cap is a hard load error rather than an uncapped axis.

Adding a skill upstream therefore costs one line here, taken deliberately, with
"nothing applies" spelled out as an explicit entry rather than reached by
omission.
"""

from dataclasses import dataclass
from types import MappingProxyType
from typing import Mapping

from robot_skills import (
    CloseGripper,
    ExtendColumn,
    Grasp,
    MoveGripper,
    NavigateTo,
    OpenGripper,
    Place,
    Skill,
    SKILL_TYPES,
)

__all__ = ['policy_for', 'SKILL_POLICIES', 'SkillPolicy', 'unclassified_skills']


@dataclass(frozen=True)
class SkillPolicy:
    """How the safety layer treats one skill of the vocabulary.

    * ``closes_jaws`` -- the skill drives the jaws shut, so measured gripper
      force is judged against the force ceiling before it runs (D19).  The
      skill must carry a ``side`` field; ``None`` there means "the backend
      picks", and every side is then checked.
    * ``clamps_column_height`` -- the skill carries a commanded column height,
      the one scalar this layer rewrites.  The skill must carry a ``height``
      field.
    * ``has_cartesian_target`` -- the skill names a point in space, so the
      collision guard can judge it.  The skill must carry a ``pose`` field.

    Every flag defaults to "does not apply".  That default is only safe because
    the table below is *exhaustive* and its exhaustiveness is enforced -- an
    unlisted skill never reaches a :class:`SkillPolicy` at all.
    """

    closes_jaws: bool = False
    clamps_column_height: bool = False
    has_cartesian_target: bool = False


#: The whole skill vocabulary, keyed by wire name, and what safety does to each.
#:
#: Keyed by :attr:`~robot_skills.skills.Skill.name` -- the same key
#: :data:`~robot_skills.skills.SKILL_TYPES` uses -- so the two are directly
#: comparable and a rename on either side is caught rather than silently
#: creating an unclassified skill.
SKILL_POLICIES: Mapping[str, SkillPolicy] = MappingProxyType({
    # Drives the base to a named location: no scalar to clamp, no jaws, and no
    # commanded pose the stub geometry could judge (the *route* is the
    # backend's, and checking it needs the real-geometry feature).
    NavigateTo.name: SkillPolicy(),
    # A commanded gripper pose: geometry, and nothing else.
    MoveGripper.name: SkillPolicy(has_cartesian_target=True),
    # Closes the jaws on an object; its target is an object id, not a pose, so
    # there is no point for the stub guard to test.
    Grasp.name: SkillPolicy(closes_jaws=True),
    # A commanded put-down pose.  Places do not close the jaws -- they open
    # them -- so force is not judged here.
    Place.name: SkillPolicy(has_cartesian_target=True),
    # The one clamped scalar in the skill API.
    ExtendColumn.name: SkillPolicy(clamps_column_height=True),
    # Deliberately unchecked, and this is a safety property rather than an
    # omission: opening is the *remedy* for over-force, so gating it on force
    # would make an over-force state unrecoverable -- the robot could never let
    # go of what it is crushing.
    OpenGripper.name: SkillPolicy(),
    CloseGripper.name: SkillPolicy(closes_jaws=True),
})


def policy_for(skill: Skill) -> SkillPolicy | None:
    """Return how this layer treats ``skill``, or ``None`` if it is unclassified.

    ``None`` is not "nothing applies" -- that is a :class:`SkillPolicy` with no
    flags set, and it is written out for the skills that mean it.  ``None``
    means *this layer has no opinion recorded*, which the gate turns into a
    refusal.
    """
    return SKILL_POLICIES.get(skill.name)


def unclassified_skills(registry: Mapping[str, type[Skill]] = SKILL_TYPES) -> tuple[str, ...]:
    """Return the wire names in ``registry`` that this layer has no policy for.

    The tripwire: called with its default argument it must return ``()``, so a
    skill added to the shared seam fails *this* package's suite until somebody
    decides what safety does about it.
    """
    return tuple(sorted(set(registry) - set(SKILL_POLICIES)))
