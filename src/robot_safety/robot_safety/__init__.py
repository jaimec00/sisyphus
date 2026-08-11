# Copyright (c) 2026 Jaime C.
#
# Use of this source code is governed by an MIT-style
# license that can be found in the LICENSE file or at
# https://opensource.org/licenses/MIT.

"""The dynamic-safety layer: clamp or abort a skill before it reaches a backend.

Decision D4 makes this layer mandatory from day one; D17 says what belongs in
it.  Limits split by *kind*, not by component:

* *"can't be done"* -- an unreachable pose, an unknown object -- is a
  **backend refusal**, decided below this layer against the backend's own
  kinematics;
* *"unsafe to continue"* -- an out-of-range column height, an axis running too
  fast, jaws squeezing too hard, the e-stop line -- is a **safety event**,
  decided here, backend-agnostically, so Mock, Sim and Real get the same
  clamps.

Pure Python: importing this package needs no ROS graph and no ROS packages.

Example::

    from robot_safety import SafetyEvent, SafetyLayer, SafetyState
    from robot_skills import ExtendColumn

    layer = SafetyLayer()                       # limits from limits.yaml
    verdict = layer.filter(ExtendColumn(9.0), SafetyState(observation))
    if isinstance(verdict, SafetyEvent):
        ...                                     # aborted: do not execute
    else:
        backend.execute(verdict.skill)          # clamped to the column stop
"""

from robot_safety.collision import (
    CollisionGuard,
    KeepOutBoxGuard,
    NullCollisionGuard,
    target_pose,
)
from robot_safety.events import SafetyEvent, SafetyEventKind
from robot_safety.layer import ClampedCall, SafetyLayer
from robot_safety.limits import (
    ColumnLimits,
    DEFAULT_LIMITS_RESOURCE,
    KeepOutBox,
    MotionLimits,
    SafetyConfigError,
    SafetyLimits,
)
from robot_safety.policy import policy_for, SKILL_POLICIES, SkillPolicy, unclassified_skills
from robot_safety.state import MotionAxis, SafetyState

__all__ = [
    'ClampedCall',
    'CollisionGuard',
    'ColumnLimits',
    'DEFAULT_LIMITS_RESOURCE',
    'KeepOutBox',
    'KeepOutBoxGuard',
    'MotionAxis',
    'MotionLimits',
    'NullCollisionGuard',
    'policy_for',
    'SafetyConfigError',
    'SafetyEvent',
    'SafetyEventKind',
    'SafetyLayer',
    'SafetyLimits',
    'SafetyState',
    'SKILL_POLICIES',
    'SkillPolicy',
    'target_pose',
    'unclassified_skills',
]
