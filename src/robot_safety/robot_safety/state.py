# Copyright (c) 2026 Jaime C.
#
# Use of this source code is governed by an MIT-style
# license that can be found in the LICENSE file or at
# https://opensource.org/licenses/MIT.

"""Safety telemetry: the dynamic state a skill is judged against.

:class:`SafetyState` *composes* the shared
:class:`~robot_skills.observation.Observation` rather than extending it.  An
observation is the **brain-facing** perception type (D3, CLAUDE.md invariant
4), and the brain never plans on jaw force or axis speed: telemetry is an input
to the safety layer alone, so widening the brain's contract with it would buy
no consumer anything.  Everything here is therefore local to ``robot_safety``
and the shared schema is consumed read-only.

The state is a *sample*, not a session.  Each call to
:meth:`~robot_safety.layer.SafetyLayer.filter` judges one sample, which is what
lets the same pure gate serve both a synchronous backend (sample once, before
execution) and a future asynchronous one (re-sample and re-ask while the motion
runs) without a redesign.
"""

from dataclasses import dataclass, field
from enum import Enum, unique
from types import MappingProxyType
from typing import Any, Mapping

from robot_skills import Observation, Side
from robot_skills.validation import as_enum, as_finite_float

__all__ = ['MotionAxis', 'SafetyState']


@unique
class MotionAxis(Enum):
    """A body axis whose speed the safety layer caps.

    The member ``value`` strings double as the ``velocity`` keys of
    ``limits.yaml``, so configuration and telemetry share one vocabulary: a
    typo becomes a load error instead of a silently uncapped axis.
    """

    BASE = 'base'
    COLUMN = 'column'
    ARM = 'arm'


def _as_measurements(
    raw: Any,
    member_type: type[Enum],
    *,
    name: str,
) -> Mapping[Any, float]:
    """Return a read-only mapping of enum member to a non-negative magnitude.

    Keys are coerced through the enum (its string value is accepted), values
    must be finite and non-negative -- these are *measured magnitudes*, and a
    negative speed or force is a sensor/units bug, not a safe reading.
    """
    if not isinstance(raw, Mapping):
        raise TypeError(f'{name} must be a mapping, got {type(raw).__name__}')
    measured: dict[Any, float] = {}
    for key, value in raw.items():
        member = as_enum(key, member_type, name=f'{name} key')
        magnitude = as_finite_float(value, name=f'{name}[{member.value}]')
        if magnitude < 0.0:
            raise ValueError(
                f'{name}[{member.value}] must be non-negative, got {magnitude!r}')
        measured[member] = magnitude
    return MappingProxyType(measured)


@dataclass(frozen=True)
class SafetyState:
    """One telemetry sample: the observation plus what only safety needs.

    * ``observation`` -- the shared world snapshot, untouched;
    * ``estop_engaged`` -- the hardware/soft e-stop line;
    * ``velocities`` -- measured axis speeds in m/s, keyed by
      :class:`MotionAxis`;
    * ``gripper_forces`` -- measured jaw force in newtons, keyed by
      :class:`~robot_skills.skills.Side`.

    Both mappings are *partial*: an absent key means "no reading available for
    that axis/side", and an axis with no reading cannot be judged against its
    cap.  A backend that can measure a quantity must report it; one that cannot
    (the Mock) leaves it out rather than reporting a fictitious zero.
    """

    observation: Observation
    estop_engaged: bool = False
    velocities: Mapping[MotionAxis, float] = field(default_factory=dict)
    gripper_forces: Mapping[Side, float] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not isinstance(self.observation, Observation):
            raise TypeError(
                'SafetyState.observation must be an Observation, '
                f'got {type(self.observation).__name__}')
        if not isinstance(self.estop_engaged, bool):
            raise TypeError(
                'SafetyState.estop_engaged must be a bool, '
                f'got {type(self.estop_engaged).__name__}')
        object.__setattr__(
            self,
            'velocities',
            _as_measurements(self.velocities, MotionAxis, name='SafetyState.velocities'),
        )
        object.__setattr__(
            self,
            'gripper_forces',
            _as_measurements(self.gripper_forces, Side, name='SafetyState.gripper_forces'),
        )

    def velocity(self, axis: MotionAxis) -> float | None:
        """Return the measured speed of ``axis`` in m/s, or ``None`` if unread."""
        return self.velocities.get(axis)

    def gripper_force(self, side: Side) -> float | None:
        """Return the measured force of one gripper in N, or ``None`` if unread."""
        return self.gripper_forces.get(side)
