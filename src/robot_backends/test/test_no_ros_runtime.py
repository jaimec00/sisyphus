# Copyright (c) 2026 Jaime C.
#
# Use of this source code is governed by an MIT-style
# license that can be found in the LICENSE file or at
# https://opensource.org/licenses/MIT.

"""Acceptance criterion: importable and runnable with no ROS 2 graph running.

Checked in a clean subprocess so nothing pytest (or another test) already
imported can mask a stray ``rclpy`` dependency.
"""

import os
import subprocess
import sys

PROBE = """
import sys

import robot_backends
import robot_skills
from robot_backends import MockBackend
from robot_skills import Grasp, NavigateTo, Place, Pose, SkillStatus

ros_modules = sorted(
    name for name in sys.modules
    if name == 'rclpy' or name.startswith(('rclpy.', 'rosidl', 'ament_index_python'))
)
assert not ros_modules, 'ROS modules imported: %s' % ros_modules

backend = MockBackend()
for skill in (
    NavigateTo('kitchen'),
    Grasp('mug_1'),
    NavigateTo('table'),
    Place(Pose.from_xyz(0.35, 2.05, 0.75)),
):
    result = backend.execute(skill)
    assert result.status is SkillStatus.OK, (skill, result.reason)

print(backend.get_observation().find_object('mug_1').pose.position.y)
"""


def test_packages_run_without_ros():
    """A bare interpreter can import both packages and run the whole loop."""
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
    assert completed.stdout.strip() == '2.05'


def test_no_source_file_imports_rclpy():
    """Neither package may reach for rclpy, even lazily inside a function."""
    import robot_backends
    import robot_skills

    for package in (robot_skills, robot_backends):
        root = os.path.dirname(package.__file__)
        for entry in sorted(os.listdir(root)):
            if not entry.endswith('.py'):
                continue
            with open(os.path.join(root, entry), encoding='utf-8') as handle:
                source = handle.read()
            assert 'import rclpy' not in source, f'{package.__name__}/{entry} imports rclpy'
