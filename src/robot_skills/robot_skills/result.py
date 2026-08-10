# Copyright (c) 2026 Jaime C.
#
# Use of this source code is governed by an MIT-style
# license that can be found in the LICENSE file or at
# https://opensource.org/licenses/MIT.

"""What a backend hands back after executing a skill: status + fresh observation.

Closed loop by construction (PROJECT.md "Feedback"): a :class:`SkillResult`
always carries the skill that ran, whether it succeeded, and the observation
taken *after* the attempt -- so the brain never has to ask a second question,
and a failed attempt still reports the (unchanged) world.

Failures carry both a machine-readable :class:`FailureCode` (so a planner or a
test can branch without string matching) and a human-readable ``reason`` (so
the LLM and the logs get the specifics: which object, which gripper, how far
out of reach).

Every failure code is additionally attributed to the layer that owns it (D17):
:data:`BACKEND_REFUSAL_CODES` versus :data:`SAFETY_EVENT_CODES`.  The split is
data, not behaviour -- it lets the brain, the backends and the safety layer
branch on *who said no* without string-matching a reason.
"""

from dataclasses import dataclass
from enum import Enum, unique
from typing import Any, Mapping, Self

from robot_skills.observation import Observation
from robot_skills.serialization import (
    check_keys,
    check_schema_version,
    ensure_mapping,
    get_enum,
    get_mapping,
    get_optional_enum,
    get_optional_str,
    JsonDict,
    JsonSerializable,
    parse_errors,
    SCHEMA_VERSION,
    SCHEMA_VERSION_KEY,
)
from robot_skills.skills import Side, Skill

__all__ = [
    'BACKEND_REFUSAL_CODES',
    'SAFETY_EVENT_CODES',
    'FailureCode',
    'SkillResult',
    'SkillStatus',
]


@unique
class SkillStatus(Enum):
    """Terminal status of a skill execution."""

    OK = 'ok'
    FAILED = 'failed'


@unique
class FailureCode(Enum):
    """Machine-readable reason a skill was refused or could not complete.

    Backends must reuse these codes rather than inventing prose categories.

    **Ownership (D17).**  Every code belongs to exactly one layer, split by the
    *kind* of limit it reports, not by the component that happens to notice it:

    * **backend refusal** -- *"can't be done"*.  The backend inspects the goal
      against the world and its own kinematics/workspace and refuses the skill
      up front, before any motion; nothing has moved.  Membership:
      :data:`BACKEND_REFUSAL_CODES`, queryable as :attr:`is_backend_refusal`.
    * **safety-layer clamp/abort** -- *"unsafe to continue"*.  The safety layer
      (D4) sits between brain-issued skills and the backend and clamps or
      aborts them, in flight if need be, reporting a safety event.  Membership:
      :data:`SAFETY_EVENT_CODES`, queryable as :attr:`is_safety_event`.

    The distinction is what the brain needs to decide what to do next: a
    refusal means *pick a different goal*, a safety event means *the motion was
    stopped mid-way, re-observe before assuming anything*.

    Every member is in exactly one of the two sets (tested), so classifying a
    new code is a decision the author has to make rather than one they can
    forget.  Over-force while closing a gripper, for example, is a safety event
    -- not a ``gripper_empty`` refusal (D19).
    """

    UNKNOWN_LOCATION = 'unknown_location'
    UNKNOWN_OBJECT = 'unknown_object'
    NOT_GRASPABLE = 'not_graspable'
    OBJECT_ALREADY_HELD = 'object_already_held'
    GRIPPER_OCCUPIED = 'gripper_occupied'
    GRIPPER_EMPTY = 'gripper_empty'
    OUT_OF_REACH = 'out_of_reach'
    OUT_OF_RANGE = 'out_of_range'
    UNSUPPORTED_SKILL = 'unsupported_skill'
    REJECTED = 'rejected'

    @property
    def is_backend_refusal(self) -> bool:
        """Return whether a backend owns this code ("can't be done")."""
        return self in BACKEND_REFUSAL_CODES

    @property
    def is_safety_event(self) -> bool:
        """Return whether the safety layer owns this code ("unsafe to continue")."""
        return self in SAFETY_EVENT_CODES


#: Codes a backend raises to refuse a skill up front, before anything moves.
#:
#: ``GRIPPER_EMPTY`` is here deliberately: placing with nothing held is a
#: precondition the backend checks before motion, not an in-flight abort.
BACKEND_REFUSAL_CODES: frozenset[FailureCode] = frozenset({
    FailureCode.UNKNOWN_LOCATION,
    FailureCode.UNKNOWN_OBJECT,
    FailureCode.NOT_GRASPABLE,
    FailureCode.OBJECT_ALREADY_HELD,
    FailureCode.GRIPPER_OCCUPIED,
    FailureCode.GRIPPER_EMPTY,
    FailureCode.OUT_OF_REACH,
    FailureCode.OUT_OF_RANGE,
    FailureCode.UNSUPPORTED_SKILL,
})

#: Codes the safety layer reports when it clamps or aborts a skill (D4/D17).
#:
#: Listed explicitly rather than derived as "everything else", so that adding a
#: dynamic-safety code (e-stop, collision abort, gripper over-force) is a
#: deliberate classification and not a silent default.
SAFETY_EVENT_CODES: frozenset[FailureCode] = frozenset({
    FailureCode.REJECTED,
})


@dataclass(frozen=True)
class SkillResult(JsonSerializable):
    """The outcome of one skill execution, with the resulting observation.

    Invariants (enforced in ``__post_init__``):

    * ``status == FAILED`` requires both a ``code`` and a ``reason``;
    * ``status == OK`` forbids a ``code`` but allows an informational
      ``reason`` (e.g. "gripper was already open").
    """

    skill: Skill
    status: SkillStatus
    observation: Observation
    reason: str | None = None
    code: FailureCode | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.skill, Skill):
            raise TypeError(
                f'SkillResult.skill must be a Skill, got {type(self.skill).__name__}')
        if not isinstance(self.status, SkillStatus):
            raise TypeError(
                f'SkillResult.status must be a SkillStatus, got {type(self.status).__name__}')
        if not isinstance(self.observation, Observation):
            raise TypeError(
                'SkillResult.observation must be an Observation, '
                f'got {type(self.observation).__name__}')
        if self.reason is not None and not isinstance(self.reason, str):
            raise TypeError(
                f'SkillResult.reason must be a string, got {type(self.reason).__name__}')
        if self.code is not None and not isinstance(self.code, FailureCode):
            raise TypeError(
                f'SkillResult.code must be a FailureCode, got {type(self.code).__name__}')
        if self.status is SkillStatus.FAILED:
            if self.code is None:
                raise ValueError('a failed SkillResult must carry a FailureCode')
            if not self.reason:
                raise ValueError('a failed SkillResult must carry a non-empty reason')
        elif self.code is not None:
            raise ValueError('a successful SkillResult must not carry a FailureCode')

    @property
    def succeeded(self) -> bool:
        """Return whether the skill completed successfully."""
        return self.status is SkillStatus.OK

    def grasped(self, side: Side) -> bool:
        """Return whether ``side``'s gripper holds a load after this skill (D19).

        The closed-loop answer to "did I get it?".  ``close_gripper`` on thin
        air *succeeds* and reports ``False`` here; an empty grip is information,
        not an error.  This reads through to the observation the result already
        carries rather than copying the flag, so the two can never disagree.
        """
        return self.observation.robot.gripper(side).grasped

    @classmethod
    def ok(
        cls,
        skill: Skill,
        observation: Observation,
        reason: str | None = None,
    ) -> 'SkillResult':
        """Build a successful result, optionally with an informational note."""
        return cls(skill=skill, status=SkillStatus.OK, observation=observation, reason=reason)

    @classmethod
    def failure(
        cls,
        skill: Skill,
        observation: Observation,
        code: FailureCode,
        reason: str,
    ) -> 'SkillResult':
        """Build a failed result from a code and a specific human-readable reason."""
        return cls(
            skill=skill,
            status=SkillStatus.FAILED,
            observation=observation,
            reason=reason,
            code=code,
        )

    def to_dict(self) -> JsonDict:
        """Return the result's JSON-safe dict form, version stamped (D18).

        The nested ``observation`` carries its own stamp as well: each type
        stamps its own wire form, so an observation lifted out of a result and
        published alone stays self-describing.  The two are the same constant.
        """
        return {
            SCHEMA_VERSION_KEY: SCHEMA_VERSION,
            'skill': self.skill.to_dict(),
            'status': self.status.value,
            'reason': self.reason,
            'code': None if self.code is None else self.code.value,
            'observation': self.observation.to_dict(),
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> Self:
        """Rebuild a :class:`SkillResult` from its dict form."""
        context = cls.__name__
        data = ensure_mapping(data, context=context)
        check_keys(
            data,
            required=('skill', 'status', 'observation'),
            optional=(SCHEMA_VERSION_KEY, 'reason', 'code'),
            context=context,
        )
        check_schema_version(data, context=context)
        with parse_errors(context):
            return cls(
                skill=Skill.from_dict(get_mapping(data, 'skill', context=context)),
                status=get_enum(data, 'status', SkillStatus, context=context),
                observation=Observation.from_dict(
                    get_mapping(data, 'observation', context=context)),
                reason=get_optional_str(data, 'reason', context=context),
                code=get_optional_enum(data, 'code', FailureCode, context=context),
            )
