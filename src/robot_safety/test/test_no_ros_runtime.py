# Copyright (c) 2026 Jaime C.
#
# Use of this source code is governed by an MIT-style
# license that can be found in the LICENSE file or at
# https://opensource.org/licenses/MIT.

"""Acceptance criterion: importable and usable with no ROS 2 runtime.

Two things ride on this.  The layer is pure data and pure decisions, so it
should be usable from a test, a notebook or a task service with no graph
running -- the same policy ``robot_skills`` and ``robot_backends`` hold
themselves to.  And it locks in the packaging choice: the default limits are
found through ``importlib.resources``, not through ``ament_index_python``'s
share directory, so a regression to the ROS-index route fails here rather than
in somebody's deployment.

Checked in a clean subprocess so nothing pytest already imported can mask it.
"""

import os
import subprocess
import sys

from robot_safety import SafetyLimits

PROBE = """
import sys

from robot_safety import SafetyLayer, SafetyLimits, SafetyState
from robot_skills import (
    ExtendColumn,
    GripperObservation,
    GripperState,
    Observation,
    Pose,
    RobotState,
    Side,
)

ros_modules = sorted(
    name for name in sys.modules
    if name == 'rclpy' or name.startswith(('rclpy.', 'rosidl', 'ament_index_python'))
)
assert not ros_modules, 'ROS modules imported: %s' % ros_modules

# The defaults must load from the packaged YAML, with no ament index in sight.
layer = SafetyLayer()
assert layer.limits == SafetyLimits.defaults()

robot = RobotState(
    pose=Pose(),
    column_height=0.4,
    grippers=tuple(
        GripperObservation(side=side, state=GripperState.OPEN, pose=Pose())
        for side in Side
    ),
)
state = SafetyState(observation=Observation(robot=robot))
print(layer.filter(ExtendColumn(99.0), state).skill.height)
"""


def test_the_layer_runs_without_ros():
    """A bare interpreter can import the package, load the limits and clamp."""
    env = dict(os.environ)
    env['PYTHONPATH'] = os.pathsep.join(path for path in sys.path if path)
    env.pop('ROS_DOMAIN_ID', None)

    completed = subprocess.run(
        [sys.executable, '-c', PROBE],
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    assert float(completed.stdout.strip()) == SafetyLimits.defaults().column.max_height
