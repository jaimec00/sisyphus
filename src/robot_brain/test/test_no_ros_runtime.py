# Copyright (c) 2026 Jaime C.
#
# Use of this source code is governed by an MIT-style
# license that can be found in the LICENSE file or at
# https://opensource.org/licenses/MIT.

"""The brain is not a ROS node (D21), and this package proves it stays that way.

``robot_brain`` used to declare ``rclpy`` because it was going to be the
planner loop.  It is now the OpenClaw agent's prompt and config, which nothing
in a ROS graph reads.  Anyone deploying the agent copies those files onto a
Raspberry Pi that has no ROS install at all, so an ``rclpy`` import here would
not be a style problem -- it would make the assets unloadable where they are
used.  Mirrors ``robot_mcp``/``robot_backends``: a clean-subprocess run plus a
static scan, because either one alone has a blind spot.
"""

import ast
import os
import subprocess
import sys

PROBE = """
import sys

from robot_brain import config_fragment, operating_prompt

assert operating_prompt().startswith('# Robot'), 'the prompt did not load'
assert 'mcp' in config_fragment(), 'the config fragment did not load'

ros_modules = sorted(
    name for name in sys.modules
    if name == 'rclpy' or name.startswith(('rclpy.', 'rosidl', 'ament_index_python'))
)
assert not ros_modules, 'ROS modules imported: %s' % ros_modules

print(len(operating_prompt()))
"""

#: Module roots this package may not reach for, at import time or lazily.
FORBIDDEN_ROOTS = ('rclpy',)

SAMPLE_WITH_A_LAZY_IMPORT = '''
"""A file whose lazy ROS import a naive check of the top of the file misses."""


def lazy():
    from rclpy.node import Node
    return Node
'''


def find_forbidden_imports(source: str, roots: tuple[str, ...] = FORBIDDEN_ROOTS) -> list[str]:
    """Return a description of every import of ``roots`` anywhere in ``source``.

    Walks the AST rather than the text, so an import inside a function body is
    caught and the word in a docstring is not.  This package's siblings run a
    wider scan (dynamic ``importlib`` calls too); the assets here are two
    files of loader code, so the simpler walk is honest about what it checks.
    """
    findings = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            findings += [
                f'line {node.lineno}: import {alias.name}'
                for alias in node.names if alias.name.split('.', 1)[0] in roots
            ]
        elif isinstance(node, ast.ImportFrom):
            # node.module is None for relative imports, which cannot be rclpy.
            if node.module and node.module.split('.', 1)[0] in roots:
                findings.append(f'line {node.lineno}: from {node.module} import ...')
    return findings


def test_the_import_detector_catches_a_lazy_import():
    """The scan below is only worth running if the detector actually detects."""
    assert find_forbidden_imports(SAMPLE_WITH_A_LAZY_IMPORT)
    assert find_forbidden_imports('import rclpy_lookalike\n"""rclpy in prose"""') == []


def test_the_assets_load_in_a_bare_interpreter():
    """Loading the prompt and the config needs nothing but Python."""
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
    # The prompt is a substantial document, not an empty file that "loaded".
    assert int(completed.stdout.strip()) > 2000


def test_no_source_file_imports_rclpy():
    """This package may not reach for rclpy, even lazily inside a function."""
    import robot_brain

    root = os.path.dirname(robot_brain.__file__)
    scanned = set()
    for directory, subdirectories, filenames in os.walk(root):
        subdirectories[:] = [name for name in subdirectories if name != '__pycache__']
        for name in sorted(filenames):
            if not name.endswith('.py'):
                continue
            path = os.path.join(directory, name)
            with open(path, encoding='utf-8') as handle:
                findings = find_forbidden_imports(handle.read())
            assert not findings, f'{os.path.relpath(path, root)}: {findings}'
            scanned.add(os.path.relpath(path, root))

    assert {'__init__.py', 'agent.py'} <= scanned
