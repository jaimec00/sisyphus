# Copyright (c) 2026 Jaime C.
#
# Use of this source code is governed by an MIT-style
# license that can be found in the LICENSE file or at
# https://opensource.org/licenses/MIT.

"""Acceptance criterion: the tool server runs with no ROS 2 graph.

An MCP client launches this server as a bare subprocess, so a stray ``rclpy``
import -- even a lazy one inside a function -- would turn "point your agent at
this command" into "source a ROS workspace and start a graph first".  Mirrors
``robot_backends/test/test_no_ros_runtime.py``: a clean-subprocess run plus a
static scan, because either one alone has a blind spot.
"""

import ast
import os
import subprocess
import sys

from robot_mcp.tools import FIXED_TOOL_NAMES
from robot_skills import SKILL_TYPES

PROBE = """
import sys

import anyio
from mcp.client import Client

from robot_mcp import build_server

async def main():
    async with Client(build_server()) as client:
        listed = await client.list_tools()
        await client.call_tool('navigate_to', {'location': 'kitchen'})
        result = await client.call_tool('grasp', {'object_id': 'mug_1'})
        assert result.structured_content['status'] == 'ok', result.structured_content
        return len(listed.tools)

count = anyio.run(main)

ros_modules = sorted(
    name for name in sys.modules
    if name == 'rclpy' or name.startswith(('rclpy.', 'rosidl', 'ament_index_python'))
)
assert not ros_modules, 'ROS modules imported: %s' % ros_modules

print(count)
"""


def test_the_server_serves_tools_without_ros():
    """A bare interpreter can build the server and run a whole grasp through it."""
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
    # Derived, not a literal: adding a skill to the seam must not break a test
    # about ROS isolation.
    assert completed.stdout.strip() == str(len(SKILL_TYPES) + len(FIXED_TOOL_NAMES))


#: Module roots this package may not reach for, at import time or lazily.
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


def lazy_dynamic_by_keyword():
    import importlib
    return importlib.import_module(name='rclpy.action')
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
    ``importlib.import_module('rclpy')`` (positional or by keyword) are all
    caught, while a lookalike module name or the word in a docstring is not.

    Known limit: a dynamic import whose module name is not a literal cannot be
    detected statically.  The clean-subprocess test above is the backstop for
    anything that actually executes on the import path.
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
            if called not in DYNAMIC_IMPORTERS:
                continue
            targets = list(node.args)
            targets += [
                keyword.value for keyword in node.keywords
                if keyword.arg in ('name', 'module')
            ]
            for target in targets:
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

    assert len(findings) == 4, findings
    assert any('from rclpy.node import' in item for item in findings)
    assert any('import rclpy.qos' in item for item in findings)
    assert any("import_module('rclpy')" in item for item in findings)
    assert any("import_module('rclpy.action')" in item for item in findings)

    # ...and it does not fire on prose or on a module that merely starts with it.
    assert find_forbidden_imports(SAMPLE_WITHOUT_IMPORTS) == []


def _python_files(root: str) -> list[str]:
    """Return every ``.py`` file under ``root``, recursively, sorted."""
    found = []
    for directory, subdirectories, filenames in os.walk(root):
        subdirectories[:] = sorted(
            name for name in subdirectories if name != '__pycache__')
        found += [
            os.path.join(directory, name)
            for name in sorted(filenames) if name.endswith('.py')
        ]
    return found


def test_no_source_file_imports_rclpy():
    """This package may not reach for rclpy, even lazily inside a function."""
    import robot_mcp

    root = os.path.dirname(robot_mcp.__file__)
    paths = _python_files(root)
    for path in paths:
        with open(path, encoding='utf-8') as handle:
            source = handle.read()
        findings = find_forbidden_imports(source)
        assert not findings, f'{os.path.relpath(path, root)}: {findings}'

    scanned = {os.path.relpath(path, root) for path in paths}
    assert {'__init__.py', '__main__.py', 'schemas.py', 'server.py', 'tools.py'} <= scanned
