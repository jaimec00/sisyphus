# Copyright (c) 2026 Jaime C.
#
# Use of this source code is governed by an MIT-style
# license that can be found in the LICENSE file or at
# https://opensource.org/licenses/MIT.

"""Robot backends: Mock now, Sim (MuJoCo) and Real later, behind one interface.

Decision D9: the brain and the skill API never learn which backend they are
driving.  :class:`~robot_backends.interface.RobotBackend` is that seam and
:class:`~robot_backends.mock_backend.MockBackend` is its first, physics-free
implementation -- pure Python, deterministic, no ROS graph required.

Example::

    from robot_backends import MockBackend
    from robot_skills import Grasp, NavigateTo, Place, Pose

    backend = MockBackend()
    backend.execute(NavigateTo('kitchen'))
    backend.execute(Grasp('mug_1'))
    backend.execute(NavigateTo('table'))
    result = backend.execute(Place(Pose.from_xyz(0.35, 2.05, 0.75)))
    assert result.succeeded
"""

from robot_backends.interface import RobotBackend
from robot_backends.mock_backend import MockBackend
from robot_backends.mock_world import (
    default_world,
    MockWorld,
    ObjectSpec,
    RobotModel,
    world_from_document,
    world_to_document,
)

__all__ = [
    'default_world',
    'MockBackend',
    'MockWorld',
    'ObjectSpec',
    'RobotBackend',
    'RobotModel',
    'world_from_document',
    'world_to_document',
]
