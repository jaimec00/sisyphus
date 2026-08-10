# Copyright (c) 2026 Jaime C.
#
# Use of this source code is governed by an MIT-style
# license that can be found in the LICENSE file or at
# https://opensource.org/licenses/MIT.

"""The one interface every robot backend implements (decision D9).

``Mock`` today, ``Sim`` (MuJoCo) and ``Real`` later, all behind these three
methods.  The interface is deliberately tiny and *total*: a legal
:class:`~robot_skills.skills.Skill` never raises, it comes back as a
:class:`~robot_skills.result.SkillResult` -- success or an attributable
failure.  That is what lets the layers above compose without special cases:

* a **safety wrapper** can implement ``RobotBackend`` itself, clamp or reject a
  skill, and delegate to the wrapped backend;
* a **ROS 2 action server** can wrap any backend by mapping a goal onto
  ``execute`` and a result onto ``SkillResult.to_dict()``;
* the **brain** never learns which backend it is driving.
"""

from abc import ABC, abstractmethod

from robot_skills import Observation, Skill, SkillResult

__all__ = ['RobotBackend']


class RobotBackend(ABC):
    """A robot (mock, simulated or real) that executes skills and reports state."""

    @abstractmethod
    def reset(self) -> Observation:
        """Return the robot and the world to their initial state.

        Returns the observation of the freshly reset world, so callers do not
        need a follow-up :meth:`get_observation` call.
        """

    @abstractmethod
    def get_observation(self) -> Observation:
        """Return an immutable snapshot of the current robot and scene state."""

    @abstractmethod
    def execute(self, skill: Skill) -> SkillResult:
        """Execute one skill and return its status plus a fresh observation.

        Implementations must be total over the skill types they accept: a
        skill that cannot be carried out returns ``status=failed`` with a
        :class:`~robot_skills.result.FailureCode` and a specific reason, and
        leaves the world state unchanged.  Raising is reserved for programming
        errors (e.g. passing something that is not a ``Skill`` at all).
        """
