# Copyright (c) 2026 Jaime C.
#
# Use of this source code is governed by an MIT-style
# license that can be found in the LICENSE file or at
# https://opensource.org/licenses/MIT.

"""The skill API: the typed contract between the LLM brain and any backend.

Pure Python -- importing this package neither needs nor starts a ROS graph.

The contract has three halves:

* **commands** -- :class:`~robot_skills.skills.Skill` and its seven concrete
  subclasses (goals, never joint angles);
* **perception** -- :class:`~robot_skills.observation.Observation`, structured
  scene data with coordinates;
* **feedback** -- :class:`~robot_skills.result.SkillResult`, a status plus the
  observation taken after the attempt.

Everything round-trips through plain JSON-safe dicts, so the same objects serve
an in-process Mock backend today and a ROS 2 action transport later.

Example::

    from robot_backends import MockBackend
    from robot_skills import Grasp, NavigateTo, Side, SkillStatus

    backend = MockBackend()
    backend.execute(NavigateTo('kitchen'))
    result = backend.execute(Grasp('mug_1'))
    assert result.status is SkillStatus.OK
    assert result.observation.robot.gripper(Side.LEFT).held_object_id == 'mug_1'
"""

from robot_skills.geometry import Point, Pose, Quaternion
from robot_skills.observation import (
    GripperObservation,
    GripperState,
    Observation,
    RobotState,
    SceneObject,
)
from robot_skills.result import FailureCode, SkillResult, SkillStatus
from robot_skills.serialization import JsonDict, JsonSerializable, SerializationError
from robot_skills.skills import (
    CloseGripper,
    ExtendColumn,
    Grasp,
    MoveGripper,
    NavigateTo,
    OpenGripper,
    Place,
    Side,
    SIDE_ORDER,
    Skill,
    skill_from_dict,
    SKILL_KEY,
    SKILL_TYPES,
)

__all__ = [
    'SIDE_ORDER',
    'SKILL_KEY',
    'SKILL_TYPES',
    'CloseGripper',
    'ExtendColumn',
    'FailureCode',
    'Grasp',
    'GripperObservation',
    'GripperState',
    'JsonDict',
    'JsonSerializable',
    'MoveGripper',
    'NavigateTo',
    'Observation',
    'OpenGripper',
    'Place',
    'Point',
    'Pose',
    'Quaternion',
    'RobotState',
    'SceneObject',
    'SerializationError',
    'Side',
    'Skill',
    'SkillResult',
    'SkillStatus',
    'skill_from_dict',
]
