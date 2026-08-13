# Copyright (c) 2026 Jaime C.
#
# Use of this source code is governed by an MIT-style
# license that can be found in the LICENSE file or at
# https://opensource.org/licenses/MIT.

"""The expand/parse gate for the robot description -- the harness PRs 2-7 extend.

Three tools, three failure modes, one per test function so a later PR gets a
precise signal instead of a fused pass/fail:

1. the ``xacro`` CLI expands ``robot.urdf.xacro`` (catches broken XML, a
   missing include, an undefined property or macro),
2. the ``check_urdf`` CLI parses the expansion (catches URDF that is
   well-formed XML but not a valid model -- a duplicate link name, a joint
   naming a link that does not exist, a disconnected tree),
3. ``urdf_parser_py`` re-parses it and the link set is asserted exactly
   (catches a degenerate expansion that parses fine but describes nothing,
   which ``check_urdf`` is happy with).

The CLIs are used rather than ``xacro.process_file`` because the CLI is what a
launch file actually runs, and rc + captured stderr is a legible failure.

Everything resolves through ``get_package_share_directory``, i.e. the
*installed* ``share/robot_description/`` tree -- never a path relative to this
file. That is deliberate: it makes the install wiring in ``setup.py`` part of
what this gate verifies, so a .xacro that exists in the source tree but never
reaches the install tree fails here instead of at robot bringup. There is no
source-tree fallback, by design. It follows that this suite runs under
``colcon test`` (which puts the package's install prefix on
``AMENT_PREFIX_PATH``) after a ``colcon build``, not against a bare checkout.

Extending this in PR2+: add the new links to ``EXPECTED_LINKS`` and add
whatever joint/limit assertions the subassembly earns. Keep the link set
exact -- a set that is allowed to grow silently stops being a gate.
"""

import os
import shutil
import subprocess

from ament_index_python.packages import get_package_share_directory
import pytest
from urdf_parser_py.urdf import URDF

#: The complete link set the top-level description must expand to. PR1 ships
#: the root frame and nothing else; every later PR extends this deliberately.
EXPECTED_LINKS = {'base_link'}

#: Subassembly files the top level includes. Empty in PR1, but they must be
#: installed or the expansion cannot resolve them.
SUBASSEMBLIES = ('base.xacro', 'column.xacro', 'arm.xacro')


def _require_tool(name):
    """Return the path to an executable on PATH, failing loudly if it is absent."""
    path = shutil.which(name)
    assert path is not None, (
        "'%s' is not on PATH; it is declared in package.xml and provided by the "
        'pixi environment -- run inside `pixi run`.' % name)
    return path


@pytest.fixture(scope='module')
def share_dir():
    """Resolve the *installed* share directory through the ament index."""
    return get_package_share_directory('robot_description')


@pytest.fixture(scope='module')
def top_level_xacro(share_dir):
    """Path to the single entry point every consumer expands."""
    return os.path.join(share_dir, 'urdf', 'robot.urdf.xacro')


@pytest.fixture(scope='module')
def expansion(top_level_xacro):
    """Run the xacro CLI once; the result feeds every assertion below."""
    return subprocess.run(
        [_require_tool('xacro'), top_level_xacro],
        capture_output=True, text=True, check=False)


@pytest.fixture(scope='module')
def expanded_urdf_path(expansion, tmp_path_factory):
    """Write the expansion to disk, where the CLI parser can read it."""
    path = tmp_path_factory.mktemp('description') / 'robot.urdf'
    path.write_text(expansion.stdout)
    return str(path)


def test_share_layout_is_installed(share_dir):
    """The urdf/ and meshes/ install dirs exist, with every source file in them."""
    urdf_dir = os.path.join(share_dir, 'urdf')
    meshes_dir = os.path.join(share_dir, 'meshes')
    assert os.path.isdir(urdf_dir), 'missing install dir: %s' % urdf_dir
    assert os.path.isdir(meshes_dir), 'missing install dir: %s' % meshes_dir
    for name in ('robot.urdf.xacro',) + SUBASSEMBLIES:
        installed = os.path.join(urdf_dir, name)
        assert os.path.isfile(installed), (
            '%s is not installed; setup.py data_files globs urdf/*, so this '
            'means the file is missing from the source tree or the workspace '
            'needs a rebuild.' % installed)


def test_xacro_expands_without_error(expansion):
    """The xacro CLI expands the top-level file with rc 0."""
    assert expansion.returncode == 0, (
        'xacro exited %d:\n%s' % (expansion.returncode, expansion.stderr))
    assert expansion.stdout.strip(), 'xacro exited 0 but produced no output'


def test_check_urdf_parses_the_expansion(expanded_urdf_path):
    """check_urdf accepts the expanded description as a valid URDF model."""
    proc = subprocess.run(
        [_require_tool('check_urdf'), expanded_urdf_path],
        capture_output=True, text=True, check=False)
    assert proc.returncode == 0, (
        'check_urdf exited %d:\n%s%s' % (proc.returncode, proc.stdout, proc.stderr))


def test_link_set_is_exactly_the_expected_links(expansion):
    """The parsed model contains exactly EXPECTED_LINKS -- no more, no fewer."""
    robot = URDF.from_xml_string(expansion.stdout)
    links = {link.name for link in robot.links}
    assert links == EXPECTED_LINKS, (
        'link set drifted: missing %s, unexpected %s' % (
            sorted(EXPECTED_LINKS - links), sorted(links - EXPECTED_LINKS)))


def test_robot_is_named(expansion):
    """The model carries the robot's name, so downstream tooling can identify it."""
    robot = URDF.from_xml_string(expansion.stdout)
    assert robot.name == 'sisyphus', 'unexpected robot name: %r' % robot.name
