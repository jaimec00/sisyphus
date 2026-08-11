# Copyright (c) 2026 Jaime C.
#
# Use of this source code is governed by an MIT-style
# license that can be found in the LICENSE file or at
# https://opensource.org/licenses/MIT.

"""Shared builders for the robot_safety tests.

Kept out of ``conftest.py`` so test modules can import the helpers directly
without relying on a module name that every package in the workspace shares.
Deliberately local: each package owns its own ``test/`` helpers, which are not
importable across package boundaries.

The limit set built here is *not* the shipped one -- the tests pick round,
obviously-synthetic numbers so a failure reads as "the layer clamped wrong",
never as "somebody retuned ``limits.yaml``".  Exactly one module
(``test_limits_config.py``) is about the shipped file itself.
"""

from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, ClassVar, Mapping, Self

import pytest
from robot_safety import SafetyLimits, SafetyState
from robot_skills import (
    CloseGripper,
    ExtendColumn,
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
    Skill,
    SKILL_TYPES,
)

#: One example of every skill in the shared vocabulary, keyed by wire name.
#:
#: Keyed off ``SKILL_TYPES`` rather than hand-listed at each use site, so a
#: skill added to the seam shows up as a missing example
#: (``test_skill_policy.py``) instead of quietly going untested here.
EXAMPLE_SKILLS: Mapping[str, Skill] = MappingProxyType({
    NavigateTo.name: NavigateTo('kitchen'),
    MoveGripper.name: MoveGripper(Side.LEFT, Pose.from_xyz(0.4, 0.2, 0.9)),
    Grasp.name: Grasp('mug_1'),
    Place.name: Place(Pose.from_xyz(0.4, 0.2, 0.9)),
    ExtendColumn.name: ExtendColumn(0.5),
    OpenGripper.name: OpenGripper(Side.RIGHT),
    CloseGripper.name: CloseGripper(Side.RIGHT),
})


def every_skill() -> list:
    """Return one pytest param per example skill, in registry order."""
    return [
        pytest.param(EXAMPLE_SKILLS[name], id=name)
        for name in sorted(EXAMPLE_SKILLS)
        if name in SKILL_TYPES
    ]


@dataclass(frozen=True)
class UnclassifiedSkill(Skill, register=False):
    """Stand-in for a skill somebody adds to the seam tomorrow.

    ``register=False`` keeps it out of the shared registry, so merely importing
    this module cannot pollute ``SKILL_TYPES`` for the rest of the session: it
    stands for the *gap* in this layer's coverage, not for a real command.
    """

    name: ClassVar[str] = 'wipe_surface'

    target: str = 'counter'

    def _payload(self) -> dict[str, Any]:
        """Return the skill's arguments, as any skill must be able to."""
        return {'target': self.target}

    @classmethod
    def _from_payload(cls, data: Mapping[str, Any]) -> Self:
        """Rebuild the stand-in from its dict form."""
        return cls(target=data['target'])


@dataclass(frozen=True)
class NameSquattingSkill(Skill, register=False):
    """A skill claiming a registered wire name while having the wrong shape.

    The policy table is keyed by wire name, so this is how a lookup by name
    could hand an object the *real* skill's policy and then reach for a field
    it does not have.  ``grasp`` promises ``object_id`` and ``side``; this one
    has neither.
    """

    name: ClassVar[str] = 'grasp'

    target: str = 'counter'

    def _payload(self) -> dict[str, Any]:
        """Return the skill's arguments, as any skill must be able to."""
        return {'target': self.target}

    @classmethod
    def _from_payload(cls, data: Mapping[str, Any]) -> Self:
        """Rebuild the stand-in from its dict form."""
        return cls(target=data['target'])


def make_gripper(
    side: Side,
    state: GripperState = GripperState.OPEN,
    held_object_id: str | None = None,
    grasped: bool | None = None,
) -> GripperObservation:
    """Build a gripper observation with a deterministic pose."""
    offset = 0.2 if side is Side.LEFT else -0.2
    return GripperObservation(
        side=side,
        state=state,
        pose=Pose.from_xyz(0.3, offset, 0.8),
        held_object_id=held_object_id,
        grasped=(held_object_id is not None) if grasped is None else grasped,
    )


def make_robot_state(**overrides: Any) -> RobotState:
    """Build a plain robot state, overridable field by field."""
    defaults: dict[str, Any] = {
        'pose': Pose(Point(1.0, 2.0, 0.0), Quaternion(0.0, 0.0, 0.7071, 0.7071)),
        'column_height': 0.4,
        'grippers': (make_gripper(Side.LEFT), make_gripper(Side.RIGHT)),
        'location': 'kitchen',
    }
    defaults.update(overrides)
    return RobotState(**defaults)


def make_observation(**overrides: Any) -> Observation:
    """Build a small observation with one graspable object."""
    defaults: dict[str, Any] = {
        'robot': make_robot_state(),
        'objects': (SceneObject('mug_1', 'mug', Pose.from_xyz(1.3, 0.2, 0.9)),),
        'known_locations': ('kitchen', 'table'),
    }
    defaults.update(overrides)
    return Observation(**defaults)


def make_state(**overrides: Any) -> SafetyState:
    """Build a telemetry sample: nominal, unless overridden.

    Nominal means "everything reads well inside :func:`make_limits`": no
    e-stop, all three axes standing still, both grippers unloaded.
    """
    defaults: dict[str, Any] = {
        'observation': make_observation(),
        'estop_engaged': False,
        'velocities': {'base': 0.0, 'column': 0.0, 'arm': 0.0},
        'gripper_forces': {Side.LEFT: 0.0, Side.RIGHT: 0.0},
    }
    defaults.update(overrides)
    return SafetyState(**defaults)


def limits_mapping(**overrides: Any) -> dict[str, Any]:
    """Return a valid limits mapping with synthetic, easy-to-read numbers."""
    defaults: dict[str, Any] = {
        'column': {'min_height': 0.0, 'max_height': 1.0},
        'velocity': {'base': 1.0, 'column': 0.5, 'arm': 2.0},
        'gripper': {'max_force': 10.0},
        'keep_out_boxes': [],
    }
    defaults.update(overrides)
    return defaults


def make_limits(**overrides: Any) -> SafetyLimits:
    """Build a limit set from :func:`limits_mapping`, section by section."""
    return SafetyLimits.from_mapping(limits_mapping(**overrides))
