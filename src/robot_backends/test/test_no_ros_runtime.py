# Copyright (c) 2026 Jaime C.
#
# Use of this source code is governed by an MIT-style
# license that can be found in the LICENSE file or at
# https://opensource.org/licenses/MIT.

"""Acceptance criterion: importable and runnable with no ROS 2 graph running.

Checked in a clean subprocess so nothing pytest (or another test) already
imported can mask a stray ``rclpy`` dependency.
"""

import ast
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


#: Module roots neither package may reach for, at import time or lazily.
FORBIDDEN_ROOTS = ('rclpy',)

#: Every way a module can be pulled in that the detector below understands.
DYNAMIC_IMPORTERS = ('import_module', '__import__')

SAMPLE_WITH_LAZY_IMPORTS = '''
"""A file whose two lazy ROS imports a naive substring grep would miss."""

import os


def lazy_from():
    from rclpy.node import Node
    return Node


def lazy_import():
    import rclpy.qos
    return rclpy.qos


def lazy_dynamic():
    import importlib
    return importlib.import_module('rclpy')
'''

SAMPLE_WITHOUT_IMPORTS = '''
"""A clean file that merely mentions rclpy in prose and in a lookalike name."""

import os

import rclpy_stub_that_is_not_rclpy


def talk_about_rclpy():
    return 'we do not import rclpy here'
'''


def _root_module(name: str) -> str:
    """Return the top-level package of a dotted module name."""
    return name.split('.', 1)[0]


def find_forbidden_imports(source: str, roots: tuple[str, ...] = FORBIDDEN_ROOTS) -> list[str]:
    """Return a description of every import of ``roots`` anywhere in ``source``.

    Walks the AST rather than the text, so a lazy ``from rclpy.node import
    Node`` inside a function body, an ``import rclpy.qos``, and an
    ``importlib.import_module('rclpy')`` are all caught, while a lookalike
    module name or the word in a docstring is not.
    """
    findings = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if _root_module(alias.name) in roots:
                    findings.append(f'line {node.lineno}: import {alias.name}')
        elif isinstance(node, ast.ImportFrom):
            # node.module is None for relative imports, which cannot be rclpy.
            if node.module and _root_module(node.module) in roots:
                findings.append(f'line {node.lineno}: from {node.module} import ...')
        elif isinstance(node, ast.Call):
            called = getattr(node.func, 'attr', None) or getattr(node.func, 'id', None)
            if called not in DYNAMIC_IMPORTERS or not node.args:
                continue
            target = node.args[0]
            if (
                isinstance(target, ast.Constant)
                and isinstance(target.value, str)
                and _root_module(target.value) in roots
            ):
                findings.append(f'line {node.lineno}: {called}({target.value!r})')
    return findings


def test_the_import_detector_catches_every_form_it_claims_to():
    """The detector is itself tested, so the scan below is not a false comfort."""
    findings = find_forbidden_imports(SAMPLE_WITH_LAZY_IMPORTS)

    assert len(findings) == 3, findings
    assert any('from rclpy.node import' in item for item in findings)
    assert any('import rclpy.qos' in item for item in findings)
    assert any("import_module('rclpy')" in item for item in findings)

    # ...and it does not fire on prose or on a module that merely starts with it.
    assert find_forbidden_imports(SAMPLE_WITHOUT_IMPORTS) == []

    # A plain substring grep would miss two of the three and would also be
    # fooled by the clean sample's lookalike import.
    assert SAMPLE_WITH_LAZY_IMPORTS.count('import rclpy') == 1


def test_no_source_file_imports_rclpy():
    """Neither package may reach for rclpy, even lazily inside a function."""
    import robot_backends
    import robot_skills

    scanned = 0
    for package in (robot_skills, robot_backends):
        root = os.path.dirname(package.__file__)
        for entry in sorted(os.listdir(root)):
            if not entry.endswith('.py'):
                continue
            with open(os.path.join(root, entry), encoding='utf-8') as handle:
                source = handle.read()
            findings = find_forbidden_imports(source)
            assert not findings, f'{package.__name__}/{entry}: {findings}'
            scanned += 1
    assert scanned >= 10, f'expected to scan both packages, only saw {scanned} files'
