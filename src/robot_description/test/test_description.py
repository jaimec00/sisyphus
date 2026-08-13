# Copyright (c) 2026 Jaime C.
#
# Use of this source code is governed by an MIT-style
# license that can be found in the LICENSE file or at
# https://opensource.org/licenses/MIT.

"""The expand/parse gate for the robot description -- the harness PRs 2-7 extend.

Four tools, four failure modes, one per test function so a later PR gets a
precise signal instead of a fused pass/fail:

1. the ``xacro`` CLI expands ``robot.urdf.xacro`` (catches broken XML, a
   missing include, an undefined property or macro),
2. the ``check_urdf`` CLI parses the expansion (catches URDF that is
   well-formed XML but not a valid model -- a duplicate link name, a joint
   naming a link that does not exist, a disconnected tree, a zero-link
   expansion),
3. ``urdf_parser_py`` re-parses it and the link set is asserted exactly. Its
   unique catch is a link that is *renamed* or silently dropped while the
   model stays valid (``base_link`` -> ``base`` passes ``check_urdf``
   happily) -- the likeliest PR2-PR7 regression, since every consumer of this
   description names links.
4. every file the description *names* (``FILE_BEARING_TAGS``: ``<mesh>``,
   ``<texture>``) is resolved and must exist on disk. Neither ``check_urdf``
   nor ``urdf_parser_py`` ever opens one, so a typo'd or uninstalled ``.stl``
   or ``.png`` is otherwise green here and red at
   ``robot_state_publisher``/RViz/MuJoCo.

Plus the two wiring asserts that have no tool: the top level really includes
the three subassemblies, and the share layout is really installed.

The CLIs are used rather than ``xacro.process_file`` because the CLI is what a
launch file actually runs, and rc + captured stderr is a legible failure.

Everything resolves through ``get_package_share_directory``, i.e. the
*installed* ``share/robot_description/`` tree -- never a path relative to this
file. That is deliberate: it makes the install wiring in ``setup.py`` part of
what this gate verifies, so a .xacro or a mesh that exists in the source tree
but never reaches the install tree fails here instead of at robot bringup.
There is no source-tree fallback, by design. It follows that this suite runs
under ``colcon test`` (which puts the package's install prefix on
``AMENT_PREFIX_PATH``) after a ``colcon build``, not against a bare checkout.

Extending this in PR2+: add the new links to ``EXPECTED_LINKS``, add any new
file-naming element to ``FILE_BEARING_TAGS``, and add whatever joint/limit
assertions the subassembly earns. Keep the link set exact -- a set that is
allowed to grow silently stops being a gate. The asset and include asserts are
over an *empty* set and a *fixed* set today; they cost nothing until PR2 adds
the first ``.stl``, and bite from that moment on.
"""

import os
import shutil
import subprocess
import xml.etree.ElementTree as ElementTree

from ament_index_python.packages import get_package_share_directory, PackageNotFoundError
import pytest
from urdf_parser_py.urdf import URDF

#: The complete link set the top-level description must expand to. PR1 ships
#: the root frame and nothing else; every later PR extends this deliberately.
EXPECTED_LINKS = {'base_link'}

#: Subassembly files the top level must include -- a wiring contract, not just
#: a list of files that happen to be installed. Deleting an <xacro:include> is
#: otherwise invisible here (the file stays installed and stays linted).
SUBASSEMBLIES = ('base.xacro', 'column.xacro', 'arm.xacro')

#: xacro's namespace, needed to find <xacro:include> in the *unexpanded* file.
XACRO_NS = 'http://www.ros.org/wiki/xacro'

#: Every element that names a file on disk via a `filename` attribute. A list,
#: not a literal in the test, because "the gate only knows about one tag" is
#: itself the bug: <texture> was invisible for exactly as long as <mesh> was
#: hardcoded. Add the tag here when the description learns to name a new kind
#: of file. Known next one, deliberately not added yet: MuJoCo's
#: <mujoco><compiler meshdir=.../></mujoco>, which is PR7's to add along with
#: the MJCF conversion that can actually test it.
FILE_BEARING_TAGS = ('mesh', 'texture')


def _require_tool(name):
    """Return the path to an executable on PATH, failing loudly if it is absent."""
    path = shutil.which(name)
    assert path is not None, (
        "'%s' is not on PATH; it is pinned in pixi.toml and declared in "
        'package.xml -- run inside `pixi run`.' % name)
    return path


def _require_expansion(expansion):
    """Return the expanded XML, or fail naming the real culprit.

    Every assertion downstream of the expansion routes through this, so a
    broken .xacro reports as one legible root cause plus N pointers to it
    rather than N raw ``Document is empty`` parse errors.
    """
    assert expansion.returncode == 0, (
        'xacro expansion failed (rc %d), so this assertion never ran -- see '
        'test_xacro_expands_without_error for the root cause.\n%s' % (
            expansion.returncode, expansion.stderr))
    return expansion.stdout


def _resolve_asset_path(filename, share_dir):
    """Resolve a URDF file reference to an absolute path, the way ROS tooling does.

    Handles the ``package://<pkg>/<rel>`` form every ROS consumer uses,
    ``file://`` and absolute paths, and treats anything else as relative to
    this package's own share directory. Naive on purpose: real ``package://``
    resolution is the same join, so the gate resolves what the runtime will.
    """
    if filename.startswith('package://'):
        pkg, _, relative = filename[len('package://'):].partition('/')
        try:
            pkg_share = get_package_share_directory(pkg)
        except PackageNotFoundError:
            pytest.fail(
                "asset reference names package '%s', which is not on the ament "
                'index: %s' % (pkg, filename))
        return os.path.join(pkg_share, relative)
    if filename.startswith('file://'):
        return filename[len('file://'):]
    if os.path.isabs(filename):
        return filename
    return os.path.join(share_dir, filename)


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
    """The urdf/ and meshes/ install dirs exist, holding the four known .xacro files.

    Scope, since the name invites reading more into it: this checks the
    top level and SUBASSEMBLIES, not every file in urdf/. A PR2-added
    urdf/wheel.xacro is covered instead by the expansion itself, which fails
    loudly if an included file never reached the install tree.
    """
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


def test_top_level_includes_every_subassembly(top_level_xacro):
    """The top level wires in exactly the three subassemblies, no more, no fewer.

    Installed-on-disk and included-in-the-robot are independent properties:
    drop an <xacro:include> and the file goes on being installed and linted
    while its links quietly leave the robot. Read from the *unexpanded* file,
    since expansion is what erases the includes.
    """
    root = ElementTree.parse(top_level_xacro).getroot()
    included = {element.get('filename')
                for element in root.iter('{%s}include' % XACRO_NS)}
    expected = set(SUBASSEMBLIES)
    assert included == expected, (
        'subassembly includes drifted in %s: missing %s, unexpected %s' % (
            top_level_xacro, sorted(expected - included),
            sorted(included - expected)))


def test_xacro_expands_without_error(expansion):
    """The xacro CLI expands the top-level file with rc 0."""
    assert expansion.returncode == 0, (
        'xacro exited %d:\n%s' % (expansion.returncode, expansion.stderr))
    assert expansion.stdout.strip(), 'xacro exited 0 but produced no output'


def test_check_urdf_parses_the_expansion(expansion, expanded_urdf_path):
    """check_urdf accepts the expanded description as a valid URDF model."""
    _require_expansion(expansion)
    proc = subprocess.run(
        [_require_tool('check_urdf'), expanded_urdf_path],
        capture_output=True, text=True, check=False)
    assert proc.returncode == 0, (
        'check_urdf exited %d:\n%s%s' % (proc.returncode, proc.stdout, proc.stderr))


def test_link_set_is_exactly_the_expected_links(expansion):
    """The parsed model contains exactly EXPECTED_LINKS -- no more, no fewer."""
    robot = URDF.from_xml_string(_require_expansion(expansion))
    links = {link.name for link in robot.links}
    assert links == EXPECTED_LINKS, (
        'link set drifted: missing %s, unexpected %s' % (
            sorted(EXPECTED_LINKS - links), sorted(links - EXPECTED_LINKS)))


def test_every_asset_reference_resolves(expansion, share_dir):
    """Every file the description names -- of any FILE_BEARING_TAGS kind -- exists.

    Empty today (PR1 ships no geometry) and deliberately so: this is the same
    shape as EXPECTED_LINKS, costing nothing until PR2 imports the first
    LeRobot .stl and load-bearing from that moment. check_urdf validates the
    model but never opens a mesh or a texture, so without this a typo'd
    filename, an asset committed to src/ but not installed, or one referenced
    and never committed at all is green here and red at bringup.
    """
    root = ElementTree.fromstring(_require_expansion(expansion))
    references = [(tag, element.get('filename'))
                  for tag in FILE_BEARING_TAGS
                  for element in root.iter(tag)]
    unnamed = ['<%s> (%s)' % (tag, 'no filename attribute' if filename is None
                              else 'empty filename attribute')
               for tag, filename in references if not filename]
    assert not unnamed, (
        'element(s) in the expansion name no file: %s' % unnamed)
    unresolved = [(tag, filename, _resolve_asset_path(filename, share_dir))
                  for tag, filename in references
                  if not os.path.isfile(_resolve_asset_path(filename, share_dir))]
    assert not unresolved, (
        '%d of %d asset reference(s) do not resolve to a file on disk. '
        'package:// and bare relative references resolve through the '
        '*installed* share tree, so an asset that is only in the source tree '
        'counts as missing -- setup.py must install it, and the workspace may '
        'need a rebuild: %s' % (
            len(unresolved), len(references),
            ['<%s> %s -> %s' % item for item in unresolved]))


def test_robot_is_named(expansion):
    """The model carries the robot's name, so downstream tooling can identify it."""
    robot = URDF.from_xml_string(_require_expansion(expansion))
    assert robot.name == 'sisyphus', 'unexpected robot name: %r' % robot.name
