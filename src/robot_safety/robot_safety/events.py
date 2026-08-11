# Copyright (c) 2026 Jaime C.
#
# Use of this source code is governed by an MIT-style
# license that can be found in the LICENSE file or at
# https://opensource.org/licenses/MIT.

"""What the safety layer *says* when a command is unsafe: a structured event.

D17 splits limits by kind: *"can't be done"* is a backend refusal, *"unsafe to
continue"* is a safety-layer clamp or abort.  This module holds the second
half's vocabulary.

A :class:`SafetyEvent` is **data, never an exception**.  Refusing a motion is a
normal outcome of a household robot's day -- the brain has to reason about it,
log it and choose a recovery -- so it travels back as an inspectable value on
the ordinary return path, not as a control-flow escape that some caller will
eventually forget to catch.

The event kinds are deliberately *local* to ``robot_safety``.  The shared
:class:`~robot_skills.result.FailureCode` enum is consumed read-only, through
the single documented mapping :attr:`SafetyEvent.failure_code`: this package
owns its own vocabulary, and the later "wire a safety layer into the loop"
feature has exactly one seam to widen when it wants finer codes on the wire.
"""

from dataclasses import dataclass
from enum import Enum, unique

from robot_safety.state import MotionAxis
from robot_skills import FailureCode, Side
from robot_skills.validation import as_finite_float, as_identifier

__all__ = ['SafetyEvent', 'SafetyEventKind']


@unique
class SafetyEventKind(Enum):
    """Why the safety layer clamped or aborted a skill."""

    ESTOP_ENGAGED = 'estop_engaged'
    COLUMN_LIMIT = 'column_limit'
    VELOCITY_EXCEEDED = 'velocity_exceeded'
    GRIPPER_OVERFORCE = 'gripper_overforce'
    COLLISION_RISK = 'collision_risk'
    #: The layer met a skill it has no policy for (see :mod:`robot_safety.policy`).
    UNCLASSIFIED_SKILL = 'unclassified_skill'


@dataclass(frozen=True)
class SafetyEvent:
    """One thing the safety layer did, and the numbers it did it on.

    The same type serves both verdicts, which is why ``clamped_value`` is
    optional:

    * returned **directly** from :meth:`~robot_safety.layer.SafetyLayer.filter`
      it is an **abort** -- the skill does not reach the backend;
    * appearing in :attr:`~robot_safety.layer.ClampedCall.clamps` it is the
      **record of a rewrite** -- a still-executing call, with the offending
      value and what it became.

    ``side`` and ``axis`` are set when the event is about one gripper or one
    body axis; ``detail`` is human-readable prose for logs, never the machine
    contract (that is ``kind`` plus the numbers).
    """

    kind: SafetyEventKind
    detail: str
    offending_value: float | None = None
    limit: float | None = None
    clamped_value: float | None = None
    side: Side | None = None
    axis: MotionAxis | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.kind, SafetyEventKind):
            raise TypeError(
                f'SafetyEvent.kind must be a SafetyEventKind, got {type(self.kind).__name__}')
        object.__setattr__(
            self, 'detail', as_identifier(self.detail, name='SafetyEvent.detail'))
        for name in ('offending_value', 'limit', 'clamped_value'):
            value = getattr(self, name)
            if value is None:
                continue
            object.__setattr__(
                self, name, as_finite_float(value, name=f'SafetyEvent.{name}'))
        if self.side is not None and not isinstance(self.side, Side):
            raise TypeError(
                f'SafetyEvent.side must be a Side or None, got {type(self.side).__name__}')
        if self.axis is not None and not isinstance(self.axis, MotionAxis):
            raise TypeError(
                'SafetyEvent.axis must be a MotionAxis or None, '
                f'got {type(self.axis).__name__}')

    @property
    def is_clamp(self) -> bool:
        """Return whether this event records a rewrite rather than an abort."""
        return self.clamped_value is not None

    @property
    def failure_code(self) -> FailureCode:
        """Return the shared :class:`FailureCode` this event maps onto.

        The one documented bridge to the shared schema (D18): every safety
        event is currently ``REJECTED``, the sole member of
        :data:`~robot_skills.result.SAFETY_EVENT_CODES`.  Widening that enum
        would edit ``robot_skills``; this package keeps its own vocabulary and
        leaves that decision to whoever wires safety events onto the wire.
        """
        return FailureCode.REJECTED
