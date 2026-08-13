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

PR2 adds a fifth tool and the base's own structural asserts: the model is
loaded by ``robot_state_publisher``, which builds a **KDL tree** and so
rejects models the two parsers above accept (and which is the first real
consumer, per the roadmap's PR8 bringup); and the wheels are checked as
*geometry* rather than as names -- exactly three ``continuous`` wheel joints,
mounted on one circle about ``base_link`` 120 degrees apart with spin axes
that come out radial once composed with their own rpy, a ``base_footprint``
one wheel radius below the axle plane, and a real inertial on every link that
is not a pure frame. None of those are visible to the link-set assert: a
holonomic base whose three wheels are stacked at the origin has the right
links, the right joints, and no chance of driving.

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

import math
import os
import shutil
import signal
import subprocess
import threading
import time
import xml.etree.ElementTree as ElementTree

from ament_index_python.packages import get_package_share_directory, PackageNotFoundError
import pytest
from urdf_parser_py.urdf import URDF

#: The complete link set the top-level description must expand to. PR1 shipped
#: the root frame and nothing else; PR2 adds the base. Every later PR extends
#: this deliberately -- a set allowed to grow silently stops being a gate.
EXPECTED_LINKS = {
    'base_link',
    'base_footprint',
    'base_chassis_link',
    'base_left_wheel_link',
    'base_back_wheel_link',
    'base_right_wheel_link',
}

#: The base's three actuated joints. These names are *not* free: LeKiwi's URDF
#: (SIGRobotics-UIUC/LeKiwi, URDF/JOINT_NAMES.md) was deliberately renamed to
#: match the LeRobot driver's motor keys, so joint-state and command dicts are
#: directly comparable between driver and model. Renaming them here would
#: silently decouple the two, which is why the set is asserted exactly.
WHEEL_JOINTS = ('base_left_wheel', 'base_back_wheel', 'base_right_wheel')

#: Links that are pure frames and so carry no mass: the root and the ground
#: projection. Every *other* link must have a real inertial (see
#: test_moving_links_have_inertia).
MASSLESS_FRAME_LINKS = frozenset({'base_link', 'base_footprint'})

#: What robot_state_publisher logs once it has built its KDL tree. Reaching
#: this line is the whole point of that test: the node accepted the model.
RSP_READY_MARKER = 'Robot initialized'

#: Hard deadline for that node to come up, seconds. Generous on purpose -- a
#: cold DDS start under a loaded `colcon test` is slower than a bare run, and
#: the failure mode this test is guarding is "the model is rejected" (which is
#: reported in well under a second), not "the node is slow".
RSP_STARTUP_TIMEOUT_S = 30.0

#: A ROS domain of this suite's own, so a robot_state_publisher started here
#: neither sees nor is seen by anything else on the machine.
RSP_DOMAIN_ID = '77'

#: Tolerances for the geometry assertions. These compare numbers xacro
#: *computed* from the same properties, so the only slack needed is float
#: round-off -- anything looser would start accepting a real mis-mount.
PLACEMENT_TOL_M = 1e-9
ANGLE_TOL_DEG = 1e-6

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


def _rotation_from_rpy(rpy):
    """Return the 3x3 rotation matrix a URDF ``rpy`` triple denotes (Rz * Ry * Rx).

    Hand-rolled rather than imported: ``numpy`` is not a declared test
    dependency of this package, and fifteen lines of trigonometry is a
    cheaper thing to carry than a dependency that exists to compute one
    matrix. The composition order is URDF's (fixed-axis roll-pitch-yaw), not
    an arbitrary Euler convention.
    """
    roll, pitch, yaw = rpy
    cos_r, sin_r = math.cos(roll), math.sin(roll)
    cos_p, sin_p = math.cos(pitch), math.sin(pitch)
    cos_y, sin_y = math.cos(yaw), math.sin(yaw)
    return (
        (cos_y * cos_p,
         cos_y * sin_p * sin_r - sin_y * cos_r,
         cos_y * sin_p * cos_r + sin_y * sin_r),
        (sin_y * cos_p,
         sin_y * sin_p * sin_r + cos_y * cos_r,
         sin_y * sin_p * cos_r - cos_y * sin_r),
        (-sin_p, cos_p * sin_r, cos_p * cos_r),
    )


def _rotate(matrix, vector):
    """Apply a 3x3 rotation matrix to a 3-vector."""
    return tuple(sum(row[i] * vector[i] for i in range(3)) for row in matrix)


def _wheel_placements(model):
    """Map each wheel joint to its (radius, angle_deg, joint), from the model itself.

    The angle and radius are *measured* off the expansion rather than compared
    against literals, so the assertions below hold whatever the xacro
    properties are retuned to -- what is being gated is the relationship
    between the three wheels, not any one number.
    """
    joints = {joint.name: joint for joint in model.joints}
    placements = {}
    for name in WHEEL_JOINTS:
        joint = joints.get(name)
        assert joint is not None, (
            "no joint named '%s'; see "
            'test_wheel_joints_are_exactly_three_continuous.' % name)
        x, y, _z = joint.origin.xyz
        placements[name] = (math.hypot(x, y),
                            math.degrees(math.atan2(y, x)) % 360.0,
                            joint)
    return placements


def _read_stream(stream, sink):
    """Drain a text stream line by line into ``sink`` until it closes."""
    for line in stream:
        sink.append(line)


def _terminate_group(process, group):
    """Take down a launched node and everything ``ros2 run`` spawned for it.

    Signalling the ``ros2 run`` wrapper alone is not enough, and the failure is
    nastier than a leaked process: the wrapper *re-spawns* the node as its own
    child, so the node survives, keeps the inherited stdout pipe open, and the
    reader thread stays blocked in ``read()`` holding the buffer lock -- at
    which point closing that pipe deadlocks the test. Hence the whole process
    group (the node is put in its own session at launch, so the group is
    exactly this node and its wrapper, never the test runner).
    """
    for sig in (signal.SIGTERM, signal.SIGKILL):
        if process.poll() is not None and not _group_alive(group):
            return
        try:
            os.killpg(group, sig)
        except ProcessLookupError:
            pass
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            continue


def _group_alive(group):
    """Return True while any process remains in ``group``."""
    try:
        os.killpg(group, 0)
    except (ProcessLookupError, PermissionError):
        return False
    return True


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


@pytest.fixture(scope='module')
def parsed_model(expansion):
    """Parse the expansion once, for the assertions that walk the model.

    The two PR1 tests that need a model parse it inline; from PR2 on there are
    enough of them that re-parsing per assertion is just noise. Read-only by
    convention -- nothing here mutates the returned model.
    """
    return URDF.from_xml_string(_require_expansion(expansion))


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


def test_wheel_joints_are_exactly_three_continuous(parsed_model):
    """The base has exactly the three named wheel joints, all ``continuous``.

    Three separate regressions share this one assertion because they are one
    fact about a holonomic base: a fourth wheel (D26 supersedes D1's 4-wheel
    base), a renamed joint (which decouples the model from the LeRobot driver
    that shares these motor keys), and a wheel authored as ``revolute`` or
    ``fixed`` (which would silently cap or freeze the wheel) are all invisible
    to ``check_urdf`` and to the link-set assert -- ``EXPECTED_LINKS`` names
    links, and a joint can be renamed or retyped without touching one.
    """
    wheel_joints = {joint.name: joint.type for joint in parsed_model.joints
                    if 'wheel' in joint.name}
    assert set(wheel_joints) == set(WHEEL_JOINTS), (
        'wheel joint names drifted: missing %s, unexpected %s' % (
            sorted(set(WHEEL_JOINTS) - set(wheel_joints)),
            sorted(set(wheel_joints) - set(WHEEL_JOINTS))))
    mistyped = {name: kind for name, kind in wheel_joints.items()
                if kind != 'continuous'}
    assert not mistyped, (
        'wheel joints must be continuous (an omniwheel has no travel limit); '
        'these are not: %s' % mistyped)


def test_wheel_mounts_are_120_degrees_apart(parsed_model):
    """The three wheels sit on one circle about ``base_link``, 120 degrees apart.

    This is the assertion that makes "3-omniwheel holonomic base" a gate
    instead of a comment. Everything else in this file would stay green with
    all three wheels stacked at the origin, mounted off the chassis instead of
    the root, or spaced 90/90/180 -- the model would still be a valid URDF with
    the right link and joint names, and would still load in
    robot_state_publisher. It is also the property the base's kinematics are
    derived from: LeRobot's LeKiwi driver maps body velocity to wheel speeds
    through a matrix built from exactly these mount angles and this radius, so
    a wheel that moves here without the driver moving with it is a robot that
    drives sideways when told to drive forward.

    The axis clause is the other half of that contract, and the easier half to
    get wrong: it is asserted *after* rotating the joint axis by the joint's
    own rpy, because the model states the axis in the wheel's rotated frame
    (``0 0 1``) and it is the composition -- not either factor -- that has to
    come out as the outward radial direction.

    Nothing here is compared against a literal dimension: the radius is read
    off the expansion itself, so retuning ``base_radius`` in the xacro is not
    a test edit, while breaking the *relationship* between the wheels is.
    """
    placements = _wheel_placements(parsed_model)

    off_root = {name: joint.parent for name, (_r, _a, joint) in placements.items()
                if joint.parent != 'base_link'}
    assert not off_root, (
        'wheel joints must hang off the root frame, so the driver kinematics '
        'and the URDF share one origin; these do not: %s' % off_root)

    out_of_plane = {name: joint.origin.xyz for name, (_r, _a, joint) in placements.items()
                    if abs(joint.origin.xyz[2]) > PLACEMENT_TOL_M}
    assert not out_of_plane, (
        'wheel axles must be coplanar with base_link (z == 0), since '
        'base_footprint is one wheel radius below it: %s' % out_of_plane)

    radii = {name: radius for name, (radius, _a, _j) in placements.items()}
    base_radius = min(radii.values())
    assert base_radius > 0, (
        'wheels sit at the origin, not on a circle around it: %s' % radii)
    assert max(radii.values()) - base_radius < PLACEMENT_TOL_M, (
        'wheels are not all the same distance from base_link, so the base is '
        'not symmetric and the driver matrix has one radius: %s' % radii)

    angles = sorted(angle for _r, angle, _j in placements.values())
    gaps = [(angles[(i + 1) % len(angles)] - angles[i]) % 360.0
            for i in range(len(angles))]
    assert all(abs(gap - 120.0) < ANGLE_TOL_DEG for gap in gaps), (
        'wheel mount angles are not evenly spaced: angles %s give gaps %s, '
        'expected three of 120 degrees' % (
            [round(a, 4) for a in angles], [round(g, 4) for g in gaps]))

    for name, (_radius, angle, joint) in sorted(placements.items()):
        axis_in_base = _rotate(_rotation_from_rpy(joint.origin.rpy), joint.axis)
        expected = (math.cos(math.radians(angle)), math.sin(math.radians(angle)), 0.0)
        assert all(abs(axis_in_base[i] - expected[i]) < PLACEMENT_TOL_M
                   for i in range(3)), (
            "%s's spin axis, rotated into base_link coordinates, is %s; it must "
            'be the outward radial direction %s at its own mount angle '
            '(%.4f deg) -- the wheel-link frame convention this model shares '
            'with upstream LeKiwi, and the composition PR6/PR7 read the wheel '
            'kinematics off (D29)' % (
                name, [round(v, 6) for v in axis_in_base],
                [round(v, 6) for v in expected], angle))


def test_base_footprint_is_the_ground_projection(parsed_model):
    """``base_footprint`` is a fixed child of ``base_link``, one wheel radius below it.

    ``EXPECTED_LINKS`` only proves the frame exists; a frame at the wrong
    height, or hung off the chassis, is exactly as valid a URDF and exactly as
    wrong for everything that consumes it (Nav2 footprints, RViz ground plane,
    any "is the robot on the floor" check). The offset is checked against the
    wheel radius read from the model's own wheel geometry rather than a
    literal, so the two cannot drift apart silently: ``base_link`` sits at axle
    height *because* that is where the wheels are.
    """
    wheel_radii = set()
    for joint_name in WHEEL_JOINTS:
        link = parsed_model.link_map[joint_name + '_link']
        assert link.collisions, '%s has no collision geometry' % link.name
        geometry = link.collisions[0].geometry
        assert hasattr(geometry, 'radius'), (
            '%s collision geometry has no radius, so the ground offset cannot '
            'be checked against it: %r' % (link.name, geometry))
        wheel_radii.add(round(geometry.radius, 12))
    assert len(wheel_radii) == 1, (
        'the three wheels have different radii %s; a holonomic base with '
        'mismatched wheels has no single ground plane' % sorted(wheel_radii))
    wheel_radius = wheel_radii.pop()

    footprint_joints = [joint for joint in parsed_model.joints
                        if joint.child == 'base_footprint']
    assert len(footprint_joints) == 1, (
        'expected exactly one joint parenting base_footprint, found %d: %s' % (
            len(footprint_joints), [joint.name for joint in footprint_joints]))
    joint = footprint_joints[0]
    assert joint.type == 'fixed', (
        'base_footprint must be rigidly attached; it is %r' % joint.type)
    assert joint.parent == 'base_link', (
        'base_footprint must hang off the root frame (base_link stays the URDF '
        'root per D27); its parent is %r' % joint.parent)
    expected = (0.0, 0.0, -wheel_radius)
    assert all(abs(joint.origin.xyz[i] - expected[i]) < PLACEMENT_TOL_M
               for i in range(3)), (
        'base_footprint is at %s; the ground is at %s, one wheel radius '
        '(%.4f m) below the axle plane' % (joint.origin.xyz, list(expected),
                                           wheel_radius))
    assert all(abs(value) < PLACEMENT_TOL_M for value in joint.origin.rpy), (
        'base_footprint must be axis-aligned with base_link; rpy is %s' % (
            joint.origin.rpy,))


def test_moving_links_have_inertia(parsed_model):
    """Every link that is not a pure frame has positive mass and inertia.

    A description with no ``<inertial>`` expands, parses, and publishes TF
    perfectly happily, and then falls over the moment anything dynamic touches
    it: MuJoCo (PR7) refuses or silently substitutes a default for a massless
    moving body, and a zero-inertia wheel is a singular equation of motion.
    Catching that here, where the model is authored, is much cheaper than
    catching it in a simulator that reports it as an integrator failure.

    ``base_link`` and ``base_footprint`` are exempt by name: they are pure
    frames, and giving a frame mass would be the actual error.
    """
    faults = []
    for link in parsed_model.links:
        if link.name in MASSLESS_FRAME_LINKS:
            assert link.inertial is None or link.inertial.mass in (None, 0.0), (
                '%s is a pure frame and must stay massless' % link.name)
            continue
        if link.inertial is None:
            faults.append('%s: no <inertial>' % link.name)
            continue
        if not link.inertial.mass or link.inertial.mass <= 0:
            faults.append('%s: mass %r' % (link.name, link.inertial.mass))
        inertia = link.inertial.inertia
        for moment in ('ixx', 'iyy', 'izz'):
            value = getattr(inertia, moment, None)
            if not value or value <= 0:
                faults.append('%s: %s %r' % (link.name, moment, value))
    assert not faults, (
        'link(s) that move need a real inertial block: %s' % faults)


def _write_rsp_params(urdf_xml, directory):
    """Write a robot_state_publisher params file carrying the description.

    A params *file* rather than ``--ros-args -p robot_description:=<xml>``:
    an override rule is parsed as a single-line YAML scalar, so a multi-line
    URDF makes rcl abort with "Couldn't parse parameter override rule" before
    the node ever sees the model -- which would make this test fail for a
    reason that has nothing to do with the description. A params file is also
    what a real bringup launch passes, so this is the shape being gated.

    Hand-rolled YAML (a block scalar) rather than ``yaml.safe_dump`` so the
    test needs no dependency beyond the ones package.xml declares.
    """
    body = ''.join('      %s' % line if line.strip() else line
                   for line in urdf_xml.splitlines(keepends=True))
    path = os.path.join(directory, 'rsp_params.yaml')
    with open(path, 'w') as handle:
        handle.write('robot_state_publisher:\n'
                     '  ros__parameters:\n'
                     '    robot_description: |\n')
        handle.write(body)
        handle.write('\n')
    return path


def test_model_loads_in_robot_state_publisher(expansion, tmp_path):
    """robot_state_publisher accepts the model -- a stricter gate than check_urdf.

    It is a different parser doing a different job: ``check_urdf`` validates
    the URDF, while robot_state_publisher goes on to build a **KDL tree** from
    it, and KDL rejects models urdfdom accepts. It is also the first consumer
    in this repo's roadmap (PR8's bringup launch is exactly this node), so
    "loads here" is the acceptance criterion in the issue rather than a proxy
    for one.

    Mechanically it is the odd test in this file because the node does not
    exit: on success it logs ``Robot initialized`` and stays up, so the pass
    condition is a marker in its output and the test must tear it down.
    Failure is the *opposite* shape -- a rejected model makes it abort in well
    under a second -- which is why an early exit is a failure here and its
    return code plus captured output is the message. There is no
    ``launch_testing`` involved: this package's pytest.ini disables that
    plugin (it is incompatible with pytest >= 8 in this environment), so the
    process is driven directly.
    """
    params_file = _write_rsp_params(_require_expansion(expansion), str(tmp_path))
    environment = dict(os.environ, ROS_DOMAIN_ID=RSP_DOMAIN_ID)
    process = subprocess.Popen(
        [_require_tool('ros2'), 'run', 'robot_state_publisher',
         'robot_state_publisher',
         '--ros-args', '--params-file', params_file],
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
        env=environment, start_new_session=True)
    group = os.getpgid(process.pid)
    output = []
    reader = threading.Thread(
        target=_read_stream, args=(process.stdout, output), daemon=True)
    reader.start()
    try:
        deadline = time.monotonic() + RSP_STARTUP_TIMEOUT_S
        while True:
            if any(RSP_READY_MARKER in line for line in list(output)):
                return
            returncode = process.poll()
            if returncode is not None:
                reader.join(timeout=5)
                pytest.fail(
                    'robot_state_publisher exited (rc %d) instead of '
                    'initializing, i.e. it rejected the description:\n%s' % (
                        returncode, ''.join(output) or '<no output>'))
            if time.monotonic() > deadline:
                pytest.fail(
                    'robot_state_publisher neither logged %r nor exited within '
                    '%.0f s:\n%s' % (RSP_READY_MARKER, RSP_STARTUP_TIMEOUT_S,
                                     ''.join(output) or '<no output>'))
            time.sleep(0.05)
    finally:
        _terminate_group(process, group)
        reader.join(timeout=10)
        process.stdout.close()


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
