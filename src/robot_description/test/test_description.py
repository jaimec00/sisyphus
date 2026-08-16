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
consumer, per the roadmap's PR8 bringup); and the base is checked as
*geometry* rather than as names -- exactly three ``continuous`` wheel joints
each driving its own link, mounted on one circle about ``base_link`` 120
degrees apart with spin axes that come out radial once composed with their own
rpy, a ``base_footprint`` one wheel radius below the axle plane, visual and
collision geometry on every link that is a body, a chassis that clears the
wheels rather than intersecting them, and a real inertial on every link that
is not a pure frame. None of those are visible to the link-set assert: a
holonomic base whose three wheels are stacked at the origin has the right
links, the right joints, and no chance of driving.

PR3 adds the column, and with it the first joint in this model that is *not*
free to spin: a prismatic ``column_lift`` carrying ``column_top``, whose
``lower``/``upper`` are the column travel ``RobotModel`` already enforces. That
makes the column the first place the URDF **owns** a number from outside
itself, so its assertions are shaped by the same rule the wheel layout taught
(below): the limits are compared against transcribed ``ROBOT_MODEL_*``
constants rather than merely against each other, and the mast, the travel and
the chassis are tied together *relationally* -- the rail must clear the chassis
it stands on and must span the travel its own carriage is allowed, both read
off the model, so retuning any of them keeps the constraint. The lift's axis is
checked after composing every rpy between it and ``base_link``, because a
column that lifts sideways satisfies "prismatic, 0.00-1.20" perfectly. Two
column assertions exist because a review round caught the claim they now pin:
the datum's height at zero travel (stated in three documents with one of the
two joint origins left out, and wrong by 585 mm) and the lift's velocity limit
against ``SAFETY_COLUMN_SPEED_CAP_MPS`` (shipped equal to the safety layer's
own cap, which makes that cap unable to bind). Both were prose nothing
executed.

The **absolute** wheel layout gets its own assert on top of those, and the
distinction matters more than it looks: every check above compares the wheels
to each other, so all of them survive rotating or permuting the mount set as a
whole -- a left/right swap passes them while the robot drives *backward* on a
forward command. ``test_wheel_mounts_match_the_lerobot_driver_matrix``
therefore rebuilds the LeRobot driver's body->wheel matrix from the parsed
model and compares it to the driver's own constant, which is the contract PR6
cashes in, stated literally. When adding a layout assertion, ask which of the
two kinds it is; relational ones are more legible, and absolute ones are the
ones that catch a model that is self-consistent and wrong.

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

Extending this in PR3+: add the new links to ``EXPECTED_LINKS``, add any new
file-naming element to ``FILE_BEARING_TAGS``, and add whatever joint/limit
assertions the subassembly earns. Keep the link set exact -- a set that is
allowed to grow silently stops being a gate. The asset assert is still over an
*empty* set: PR2 authored the base from primitives and vendored no meshes
(D29), so it costs nothing until the first PR that lands real geometry
files -- expected to be the arms -- and bites from that moment on.
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
#: the root frame and nothing else; PR2 adds the base, PR3 the column's static
#: mast and its moving carriage. Every later PR extends this deliberately -- a
#: set allowed to grow silently stops being a gate.
EXPECTED_LINKS = {
    'base_link',
    'base_footprint',
    'base_chassis_link',
    'base_left_wheel_link',
    'base_back_wheel_link',
    'base_right_wheel_link',
    'column_rail_link',
    'column_top',
}

#: The base's three actuated joints. These names are *not* free: LeKiwi's URDF
#: (SIGRobotics-UIUC/LeKiwi, URDF/JOINT_NAMES.md) was deliberately renamed to
#: match the LeRobot driver's motor keys, so joint-state and command dicts are
#: directly comparable between driver and model. Renaming them here would
#: silently decouple the two, which is why the set is asserted exactly.
WHEEL_JOINTS = ('base_left_wheel', 'base_back_wheel', 'base_right_wheel')

#: The LeRobot LeKiwi driver's own body->wheel kinematics, transcribed from
#: `_body_to_wheel_raw` in `lerobot/robots/lekiwi/lekiwi.py`::
#:
#:     angles = np.radians(np.array([240, 0, 120]) - 90)   # left, back, right
#:     m = np.array([[np.cos(a), np.sin(a), base_radius] for a in angles])
#:
#: Keyed by joint name rather than kept as an ordered list, because the pairing
#: of motor key to row *is* the fact under test: a list would re-map silently if
#: anyone reordered it. Row i of `m` is wheel i's rolling direction plus the
#: lever arm, i.e. `m @ [vx, vy, omega]` is that wheel's linear speed.
DRIVER_ROLLING_ANGLES_DEG = {
    'base_left_wheel': 240.0 - 90.0,
    'base_back_wheel': 0.0 - 90.0,
    'base_right_wheel': 120.0 - 90.0,
}

#: The driver's `base_radius` default, metres -- the third column of `m`.
DRIVER_BASE_RADIUS_M = 0.125

#: The driver's `wheel_radius` default, metres. It never appears in `m`, which
#: is why it is easy to forget, and it is the constant the driver *divides* by
#: in both directions (`wheel_angular_speeds = wheel_linear_speeds /
#: wheel_radius` on the way out, `wheel_linear_speeds = wheel_radps *
#: wheel_radius` on the way back). A model that disagrees with it is not
#: visibly broken anywhere: it just drives at the wrong speed and reports the
#: same error back as odometry.
DRIVER_WHEEL_RADIUS_M = 0.05

#: Tolerance for the driver-matrix comparison. Its own constant rather than
#: PLACEMENT_TOL_M because two of the three columns are direction cosines, not
#: metres; 1e-9 is right for both, but a metre-named constant should not be
#: silently reused as a dimensionless one. Observed error on the shipped model
#: is ~2e-16, so there are seven orders of headroom.
DRIVER_MATRIX_TOL = 1e-9

#: The column's travel, transcribed from `RobotModel.min_column_height` and
#: `RobotModel.max_column_height` in
#: `src/robot_backends/robot_backends/mock_world.py` -- the numbers the Mock
#: backend, the safety layer and the brain's prompt all already enforce, and
#: which the URDF's `column_lift` limits must equal. Metres.
#:
#: Transcribed rather than imported, the same way the DRIVER_* constants above
#: transcribe LeRobot's: `robot_description` takes no dependency on
#: `robot_backends` (D30 declined the analogous cross-seam test edge, and PR6
#: *inverts* this one by making `RobotModel` read the URDF, so an edge landed
#: here would have to be torn back out). The cost is real and worth stating
#: plainly: this is a hand-typed copy, so it can drift from its source without
#: anything noticing -- exactly the weakness D30 records for its own ledger.
#: What closes it is PR6: once `RobotModel`'s defaults come from this URDF the
#: copy disappears rather than needing to be maintained.
ROBOT_MODEL_MIN_COLUMN_HEIGHT_M = 0.0
ROBOT_MODEL_MAX_COLUMN_HEIGHT_M = 1.20

#: The safety layer's column speed cap, transcribed from `velocity.column` in
#: `src/robot_safety/robot_safety/limits.yaml` -- the *policy* limit on how
#: fast this robot may move its lift, m/s.
#:
#: It is here because the URDF's `<limit velocity>` is a different quantity
#: pointing at the same axis: capability, i.e. what the mechanism can do and
#: what MoveIt and ros2_control plan and clamp against. Policy has to sit
#: strictly *below* capability or the clamp cannot bind -- set them equal and
#: the safety layer stops constraining anything that trusts the URDF, with
#: nothing anywhere going red. That is not hypothetical: this description
#: shipped 0.15 for exactly one review round, having independently guessed the
#: cap's own value.
#:
#: Same transcription residue as the ROBOT_MODEL_* constants above, and
#: deliberately the same trade: no `robot_safety` test dependency (a
#: description package must not grow an edge to the safety layer to check one
#: inequality), so this is a hand-typed copy that can drift from `limits.yaml`.
#: Drift is not symmetric, and the direction that matters is the one this
#: assertion exists to prevent. A cap *lowered* in `limits.yaml` leaves this
#: comparing against a stale, higher number -- the assertion is merely stricter
#: than it needs to be, and the property still holds. A cap *raised above the
#: URDF's capability* is the failure: policy would then exceed capability (the
#: inversion `column.xacro` ranks as worse than the equality this constant was
#: added to catch, and says why) and **this test would still pass**, because it
#: compares against the copy rather than against `limits.yaml`. Measured, at
#: the scope this sentence claims -- all nine packages: raising the real cap to
#: 0.30 leaves every one of them green except `robot_brain`'s prompt-envelope
#: test, which objects to the prompt, not to the URDF. Nothing closes it *from
#: here* short of reading the cap live, which would cost the dependency edge
#: above. It could be closed from a third place that legitimately depends on
#: both -- the workspace-tooling suite, or PR8's bringup -- at no cost to this
#: package's dependency graph; that is an option, not a plan, and nobody owns
#: it. PR6/PR7, which give the lift a real actuator model, are where to
#: revisit the trade.
SAFETY_COLUMN_SPEED_CAP_MPS = 0.15

#: Links that are pure frames and so carry no mass: the root and the ground
#: projection. Every *other* link must have a real inertial (see
#: test_moving_links_have_inertia).
#:
#: Admission rule, written down so it stops being re-litigated per PR: a link
#: joins this set only if it corresponds to **no physical body** and exists to
#: serve an outside convention (`base_link` is the URDF root frame;
#: `base_footprint` is the ground projection Nav2 and RViz expect). "Computing
#: an inertia for it was inconvenient" is never a reason. Adding a link here
#: exempts it from *both* the geometry check and the inertia check with no
#: other signal anywhere in this suite -- it is the one rug in the gate, which
#: is why PR3's rail and carriage, both real solids, stayed out of it.
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


def _require_identity_origin(origin, what):
    """Assert a URDF ``<origin>`` is absent or the identity transform.

    Every dimension this file reads off a shape -- a wheel's radius, the
    chassis puck's height -- is then used as if it were expressed in the *link*
    frame. A ``<origin>`` on the shape, or a rotation on the joint that places
    the link, silently breaks that: the number is still read correctly and now
    means something else. So the assertions that measure are paired with an
    assertion that they *may* measure. This is deliberately narrow -- it is
    applied only where a dimension is consumed, not to the description at
    large, because offset geometry is perfectly normal and PR3/PR4 will have
    plenty of it. A later PR that gives the base offset geometry should
    compose the transform here rather than delete this.
    """
    if origin is None:
        return
    xyz = tuple(origin.xyz or (0.0, 0.0, 0.0))
    rpy = tuple(origin.rpy or (0.0, 0.0, 0.0))
    assert all(abs(value) < PLACEMENT_TOL_M for value in xyz + rpy), (
        '%s carries a non-identity <origin> (xyz=%s rpy=%s). The clearance and '
        'ground-plane assertions read dimensions straight off this shape and '
        'treat them as link-frame quantities, so an offset or rotated shape '
        'would make them measure the wrong thing while still passing.' % (
            what, list(xyz), list(rpy)))


def _collision_cylinder(model, link_name):
    """Return the ``(radius, length)`` of a link's first collision cylinder.

    Fails naming the link if it has no collision geometry, if that geometry is
    not a cylinder, or if it is displaced from the link frame -- so a shape
    change reports as itself rather than as an ``AttributeError`` three
    assertions later or, worse, as a number that is quietly about a different
    place.
    """
    link = model.link_map[link_name]
    assert link.collisions, (
        '%s has no <collision> geometry; see '
        'test_solid_links_have_visual_and_collision_geometry.' % link_name)
    collision = link.collisions[0]
    geometry = collision.geometry
    assert hasattr(geometry, 'radius') and hasattr(geometry, 'length'), (
        "%s's collision geometry is not a cylinder, so the dimensions this "
        'assertion needs cannot be read off it: %r' % (link_name, geometry))
    _require_identity_origin(collision.origin, "%s's <collision>" % link_name)
    return geometry.radius, geometry.length


def _box_geometry(shape, what):
    """Return a ``<visual>``/``<collision>`` box's ``(size, offset)`` in link coordinates.

    The box-shaped sibling of ``_collision_cylinder``, and deliberately *not*
    the same contract: it returns the shape's own offset instead of requiring
    the identity, because the column's carriage is displaced from its link
    frame on purpose -- ``column_top``'s frame is the arm/head mount datum and
    the body hangs below it -- so forbidding an offset here would be wrong
    rather than protective. Callers compose the offset themselves, which is
    what ``_require_identity_origin``'s own docstring asks a later PR to do
    instead of deleting it.

    A *rotation* is still refused: every caller reads the returned z extent as
    a height in the link frame, and a tipped box makes that number mean
    something else while parsing perfectly well.
    """
    geometry = shape.geometry
    size = getattr(geometry, 'size', None)
    assert size is not None and len(size) == 3, (
        '%s is not a box, so the dimensions this assertion needs cannot be '
        'read off it: %r' % (what, geometry))
    origin = shape.origin
    xyz = tuple(origin.xyz or (0.0, 0.0, 0.0)) if origin is not None else (0.0, 0.0, 0.0)
    rpy = tuple(origin.rpy or (0.0, 0.0, 0.0)) if origin is not None else (0.0, 0.0, 0.0)
    assert all(abs(value) < PLACEMENT_TOL_M for value in rpy), (
        '%s is rotated (rpy=%s). Its z extent is read as a height in the link '
        'frame, which only means anything while the two are aligned.' % (
            what, list(rpy)))
    return tuple(float(value) for value in size), xyz


def _collision_box(model, link_name):
    """Return the ``(size, offset)`` of a link's first collision box."""
    link = model.link_map[link_name]
    assert link.collisions, (
        '%s has no <collision> geometry; see '
        'test_solid_links_have_visual_and_collision_geometry.' % link_name)
    return _box_geometry(link.collisions[0], "%s's <collision>" % link_name)


def _axis_aligned_joint(model, joint_name):
    """Return a joint by name, asserting its origin carries no rotation.

    The column's arithmetic adds z offsets taken from different frames -- a
    joint origin here, half a collision box there -- and that addition only
    means anything while every frame on the way up is aligned with
    ``base_link``. Tip one of them and a mast can be lying inside the chassis
    with the sums still passing. Same rationale as ``_require_identity_origin``
    one level up, for joints rather than shapes; a later PR that genuinely
    needs a rotated column joint should compose the transform here rather than
    drop the check.
    """
    joint = model.joint_map.get(joint_name)
    assert joint is not None, (
        "no joint named '%s'; the column's joints are named in "
        'test_column_lift_is_the_models_only_prismatic_joint.' % joint_name)
    rpy = _joint_rpy(joint)
    assert all(abs(value) < PLACEMENT_TOL_M for value in rpy), (
        '%s is rotated (rpy=%s); the height arithmetic below treats its child '
        "frame as parallel to its parent's." % (joint_name, list(rpy)))
    return joint


def _joint_xyz(joint):
    """Return a joint origin's xyz triple, treating a missing origin as no offset.

    URDF makes the ``<origin>`` and its ``xyz`` optional and ``urdf_parser_py``
    reports ``None`` for either, so every read of a placement goes through
    here rather than through ``joint.origin.xyz`` directly.
    """
    if joint.origin is None or joint.origin.xyz is None:
        return (0.0, 0.0, 0.0)
    return tuple(float(value) for value in joint.origin.xyz)


def _joint_z(joint):
    """Return a joint origin's z offset, treating a missing origin as zero."""
    return _joint_xyz(joint)[2]


def _joint_rpy(joint):
    """Return a joint origin's rpy triple, treating a missing origin as no rotation.

    URDF makes both the ``<origin>`` and its ``rpy`` optional, and
    ``urdf_parser_py`` faithfully reports ``None`` for either, which is an
    ``AttributeError`` three lines into any rotation composition.
    """
    if joint.origin is None or joint.origin.rpy is None:
        return (0.0, 0.0, 0.0)
    return tuple(float(value) for value in joint.origin.rpy)


def _limit_attributes(expansion, joint_name):
    """Return the attributes a joint's ``<limit>`` element *literally declares*.

    Read off the raw expansion rather than the parsed model, because URDF --
    and faithfully, ``urdf_parser_py`` -- fills in defaults: an omitted
    ``lower`` comes back as ``0.0``, indistinguishable from a stated ``0.0``.
    Everywhere else in this file the parsed model is the right thing to assert
    against; this is the exception, because the claim being gated is that the
    description *states* a bound, not that something downstream infers the same
    number from a default.
    """
    root = ElementTree.fromstring(_require_expansion(expansion))
    for joint in root.iter('joint'):
        if joint.get('name') == joint_name:
            limit = joint.find('limit')
            assert limit is not None, (
                '%s declares no <limit> element; see '
                'test_check_urdf_parses_the_expansion.' % joint_name)
            return dict(limit.attrib)
    pytest.fail("the expansion contains no joint named '%s'" % joint_name)


def _lift_limit(model):
    """Return ``column_lift``'s ``<limit>``, failing legibly if it has none.

    Same job as ``_require_expansion`` one level down: a prismatic joint with
    no limit is rejected by ``check_urdf`` *and* by ``robot_state_publisher``,
    so the three assertions that read one would otherwise report that single
    root cause as three ``AttributeError``s on ``None``.
    """
    limit = model.joint_map['column_lift'].limit
    assert limit is not None, (
        'column_lift has no <limit>, so this assertion never ran -- see '
        'test_check_urdf_parses_the_expansion for the root cause. A prismatic '
        'joint without limits is unbounded travel and neither parser accepts '
        'it.')
    return limit


def _axis_in_base_link(model, joint):
    """Express a joint's axis in ``base_link`` coordinates, composing every rpy above it.

    Walks the parent chain rather than composing one joint's rpy, because the
    axis a consumer sees is the product of every frame between the joint and
    the root: PR3's lift hangs off the rail, which hangs off ``base_link``, and
    a later PR is free to add another link in between. Reuses
    ``_rotation_from_rpy``/``_rotate`` rather than re-deriving them.
    """
    assert joint.axis is not None, (
        '%s declares no <axis>, which URDF silently defaults to (1, 0, 0) -- a '
        'joint that travels forward rather than wherever it was meant to' % (
            joint.name,))
    vector = _rotate(_rotation_from_rpy(_joint_rpy(joint)), tuple(joint.axis))
    link = joint.parent
    while link != 'base_link':
        assert link in model.parent_map, (
            "%r has no parent joint but is not base_link, so %s's axis cannot "
            'be resolved into the root frame' % (link, joint.name))
        joint_name, parent = model.parent_map[link]
        vector = _rotate(_rotation_from_rpy(_joint_rpy(model.joint_map[joint_name])), vector)
        link = parent
    return vector


def _wheel_radius(model):
    """Return the wheel radius the model itself states, asserting the three agree.

    Read off the wheels' own collision cylinders rather than hardcoded, so the
    assertions that depend on it (the ground offset, the chassis clearance)
    track a retuned ``wheel_radius`` instead of drifting from it.
    """
    radii = {round(_collision_cylinder(model, name + '_link')[0], 12)
             for name in WHEEL_JOINTS}
    assert len(radii) == 1, (
        'the three wheels have different radii %s; a holonomic base with '
        'mismatched wheels has no single ground plane' % sorted(radii))
    return radii.pop()


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

    Four regressions share this one assertion because they are one fact about
    the base's actuation: a fourth wheel (D26 supersedes D1's 4-wheel base), a
    renamed joint (which decouples the model from the LeRobot driver that
    shares these motor keys), a wheel authored as ``revolute`` or ``fixed``
    (which would silently cap or freeze it), and a joint wired to a link other
    than its own are all invisible to ``check_urdf`` and to the link-set
    assert -- ``EXPECTED_LINKS`` names links, and a joint can be renamed,
    retyped or re-parented without changing one. The last of those is checked
    rather than left to the macro that currently makes it true, because "the
    generator happens to be correct" is not a property the gate can rely on.
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
    joints = {joint.name: joint for joint in parsed_model.joints}
    miswired = {name: joints[name].child for name in WHEEL_JOINTS
                if joints[name].child != name + '_link'}
    assert not miswired, (
        'each wheel joint must drive the link named after it; cross-wiring two '
        'of them leaves the joint set, the link set and the mount geometry all '
        'intact while moving the wrong wheel: %s' % miswired)


def test_wheel_mounts_are_120_degrees_apart(parsed_model):
    """The three wheels sit on one circle about ``base_link``, 120 degrees apart.

    This makes "3-omniwheel holonomic base" a shape the gate knows: every
    other assertion in this file stays green with all three wheels stacked at
    the origin, mounted off the chassis instead of the root, tilted out of the
    axle plane, or spaced 90/90/180 -- the model would still be a valid URDF
    with the right links and joints, and would still load in
    robot_state_publisher.

    **Scope, stated because it is easy to over-read:** every clause here
    compares measured quantities *to each other*, so all of them are invariant
    under rotating or permuting the mount set as a whole. Swap
    ``base_left_wheel`` with ``base_right_wheel``, or rotate all three mounts
    by 40 degrees, and this test is still green while the model now disagrees
    with the driver about which motor sits where. Pinning the absolute
    name-to-direction mapping is a different assertion and belongs to
    ``test_wheel_mounts_match_the_lerobot_driver_matrix``; this one is the
    legible symmetry check that says *how* the layout broke.

    The axis clause is the half most easily got wrong: it is asserted *after*
    rotating the joint axis by the joint's own rpy, because the model states
    the axis in the wheel's rotated frame (``0 0 1``) and it is the
    composition -- not either factor -- that has to come out radial.

    Nothing here is compared against a literal dimension: the radius is read
    off the expansion itself, so retuning ``base_radius`` in the xacro is not
    a test edit here (it *is* one in the driver-matrix test, correctly -- that
    number belongs to the driver).
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


def test_wheel_mounts_match_the_lerobot_driver_matrix(parsed_model):
    """The model reproduces the LeRobot driver's body->wheel matrix, row for row.

    This is the contract PR6 cashes in, asserted literally rather than as a
    property of the layout. The driver
    (``lerobot/robots/lekiwi/lekiwi.py::_body_to_wheel_raw``) turns a commanded
    body velocity into a speed for each named motor through a fixed 3x3
    matrix; the URDF is only a correct description of that robot if the matrix
    rebuilt from where the wheels actually sit agrees with it. Row *i* is
    rebuilt as ``(d_x, d_y, radius_i)`` -- the wheel's rolling direction
    ``d = z x axis`` plus its lever arm -- and compared to the driver's own
    ``(cos a_i, sin a_i, base_radius)``.

    ``d`` is taken from the joint's **actual** spin axis (composed with its
    rpy, then normalised) rather than inferred from the mount angle, so this
    test stands on its own: a wheel at the right angle with a tangential or
    vertical axis would otherwise rebuild a correct-looking row and be caught
    only by the axis clause next door, leaving this one silently dependent on
    a neighbour.

    Why this and not just the symmetry checks next door: those compare the
    wheels to each other, so they hold under any rotation or permutation of
    the mount set, and a left/right swap or a cyclic 120-degree shift passes
    every one of them while the robot drives backward (or 120 degrees off) on
    a "forward" command. The mapping from *motor key* to *body direction* is
    absolute, not relational, and this is the only assertion that pins it.

    It follows that both sourced numbers are deliberately compared against
    literals here -- ``base_radius`` in every row's third column, and
    ``wheel_radius`` separately below. Neither is this model's to retune: they
    are the driver's, and changing one without changing the driver is exactly
    the regression being gated. ``wheel_radius`` never appears in the matrix,
    which is why it needs its own clause; the driver divides by it on the way
    out and multiplies by it on the way back, so a model that disagrees drives
    at the wrong speed *and* reports the same error back as odometry.
    """
    placements = _wheel_placements(parsed_model)
    mismatched = []
    for name, rolling_angle_deg in sorted(DRIVER_ROLLING_ANGLES_DEG.items()):
        radius, mount_angle_deg, joint = placements[name]
        axis = _rotate(_rotation_from_rpy(joint.origin.rpy), joint.axis)
        norm = math.sqrt(sum(value * value for value in axis))
        assert norm > DRIVER_MATRIX_TOL, (
            "%s's spin axis is degenerate: %s" % (name, joint.axis))
        axis = tuple(value / norm for value in axis)
        # Rolling direction d = z_hat x axis, in base_link coordinates.
        model_row = (-axis[1], axis[0], radius)
        rolling = math.radians(rolling_angle_deg)
        driver_row = (math.cos(rolling), math.sin(rolling), DRIVER_BASE_RADIUS_M)
        if any(abs(model_row[i] - driver_row[i]) > DRIVER_MATRIX_TOL
               for i in range(3)):
            mismatched.append(
                '%s (mounted at %.4f deg, radius %.4f): model row %s != driver '
                'row %s' % (name, mount_angle_deg, radius,
                            [round(v, 6) for v in model_row],
                            [round(v, 6) for v in driver_row]))
    assert not mismatched, (
        "the model's wheel layout does not reproduce the LeRobot driver's "
        'kinematic matrix, so commanding this base through that driver would '
        'move it in the wrong direction. Note the driver builds its rows from '
        '`radians([240, 0, 120] - 90)`, which are *rolling directions*: the '
        'corresponding mount angles are 60/180/300 (D29). Mismatches: %s' % (
            mismatched,))

    wheel_radius = _wheel_radius(parsed_model)
    assert abs(wheel_radius - DRIVER_WHEEL_RADIUS_M) < DRIVER_MATRIX_TOL, (
        "the model's wheel radius is %.4f m but the LeRobot driver divides by "
        '%.4f m to turn a commanded body velocity into motor speed, and '
        'multiplies by it again to turn wheel feedback back into odometry. A '
        'mismatch is not visible anywhere in the matrix above: the base simply '
        'moves at %.3fx the commanded speed and reports the error back as its '
        'own position estimate.' % (
            wheel_radius, DRIVER_WHEEL_RADIUS_M,
            wheel_radius / DRIVER_WHEEL_RADIUS_M))


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
    wheel_radius = _wheel_radius(parsed_model)

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


def test_column_lift_is_the_models_only_prismatic_joint(parsed_model):
    """``column_lift`` is prismatic, carries ``column_top`` off the rail, and is unique.

    Four faults share one assertion because they are one fact about the
    column's actuation, and every one of them is invisible to everything else
    here: ``EXPECTED_LINKS`` names links, so the lift can be retyped ``fixed``
    (a column that cannot lift), renamed (decoupling the URDF from the
    ``column_lift`` key `RobotModel`/PR6 and the skill layer speak), or
    re-parented onto ``base_link`` -- which is kinematically identical while
    ``column_rail_joint`` is fixed and is exactly why it needs asserting: the
    carriage wraps the mast, so the two solids interpenetrate at every joint
    value, and only the parent/child arrangement describes that honestly. It is
    also the arrangement collision tooling is *expected* to filter (a generated
    MoveIt ACM disables adjacent pairs; MuJoCo excludes parent/child bodies),
    where siblings under ``base_link`` would be filtered by neither -- but that
    half is **unverified until PR7 builds the MJCF**, and a fixed rail joint may
    be fused into ``base_link`` there anyway, so it is not what this assertion
    rests on. All three faults pass ``check_urdf`` and
    ``robot_state_publisher`` without complaint.

    The uniqueness clause is a deliberate ratchet in the same spirit as
    ``EXPECTED_LINKS``: today one prismatic joint means one lift, so a second
    one appearing is a mistake. PR5's parallel-jaw gripper may well be a
    legitimate second prismatic joint -- when it lands, this set grows by an
    explicit edit naming it, which is the point.
    """
    prismatic = {joint.name for joint in parsed_model.joints
                 if joint.type == 'prismatic'}
    assert prismatic == {'column_lift'}, (
        'the model must have exactly one prismatic joint, the column lift: '
        'missing %s, unexpected %s' % (
            sorted({'column_lift'} - prismatic), sorted(prismatic - {'column_lift'})))
    lift = parsed_model.joint_map['column_lift']
    assert lift.parent == 'column_rail_link' and lift.child == 'column_top', (
        'column_lift must carry column_top along column_rail_link; it is wired '
        '%s -> %s. A carriage rides its own mast: the two interpenetrate by '
        'construction, and only the parent/child arrangement says so -- as '
        'siblings under base_link the model states no relationship between two '
        'solids that are always in contact.' % (lift.parent, lift.child))


def test_column_lift_limits_are_the_robot_model_column_bounds(parsed_model, expansion):
    """``column_lift``'s travel limits equal `RobotModel`'s column bounds, and say so.

    The first place this description owns a number that belongs to something
    outside it, and therefore the column's answer to D29's lesson: a gate built
    only from internal consistency checks certifies self-consistency, and a
    wrong model can be perfectly self-consistent. Every other column assertion
    in this file is relational -- the mast clears the chassis, the mast spans
    the travel, the axis is vertical -- and all of them stay green for a lift
    with 0.5 m of travel, or 5.0 m. Only this one says *which* travel, and it
    is the travel the Mock backend validates commands against, the safety
    layer clamps to, and the brain's prompt quotes at the planner.

    Asserted against transcribed constants, not against a live import (see
    ROBOT_MODEL_*_COLUMN_HEIGHT_M for why the copy and what retires it).

    Both bounds are then checked a second time **as attributes of the raw
    expansion**, which is not belt-and-braces: URDF defaults an omitted
    ``lower`` to 0, so deleting ``lower="${min_column_height}"`` from the
    ``<limit>`` leaves the parsed model reading 0.0 and this whole suite green
    while the description no longer *states* the bound at all. The acceptance
    criterion is that the URDF owns these two numbers, and a number that is
    only true by someone else's default is not owned.
    """
    limit = _lift_limit(parsed_model)
    bounds = ((limit.lower, ROBOT_MODEL_MIN_COLUMN_HEIGHT_M, 'lower'),
              (limit.upper, ROBOT_MODEL_MAX_COLUMN_HEIGHT_M, 'upper'))
    wrong = ['%s is %r, RobotModel says %r' % (which, got, want)
             for got, want, which in bounds
             if got is None or abs(got - want) > PLACEMENT_TOL_M]
    assert not wrong, (
        "column_lift's limits disagree with RobotModel's column travel "
        '(min_column_height / max_column_height in '
        "robot_backends/mock_world.py): %s. These are the carriage's travel "
        "along the rail, measured from the joint's own origin; the datum's "
        'height above base_link is both joint origins plus the joint value '
        '(0.195 + q today), and that mount offset is deliberately not folded '
        'into the limits.' % wrong)

    declared = _limit_attributes(expansion, 'column_lift')
    missing = [name for name in ('lower', 'upper') if name not in declared]
    assert not missing, (
        "column_lift's <limit> does not declare %s. URDF defaults an omitted "
        'lower to 0, which happens to equal the bound today, so the model '
        'parses to the right number without stating it -- and would keep '
        'parsing to 0 if RobotModel ever moved.' % missing)


def test_column_lift_declares_positive_effort_and_velocity_limits(parsed_model):
    """The lift's effort and velocity limits are present and strictly positive.

    Absence is not the fault this catches -- ``check_urdf`` and
    ``urdf_parser_py`` both refuse to parse a ``<limit>`` missing either
    attribute, so a description without them never reaches this line. **Zero
    is**: ``<limit effort="0" velocity="0">`` parses cleanly, loads in
    ``robot_state_publisher``, satisfies every other column assertion here, and
    describes a lift that cannot move -- MoveIt will not plan through a joint
    with no velocity budget and ``ros2_control`` will not command one. It is
    also the shape a placeholder decays into when somebody "removes" a number
    they do not have.

    Only presence and sign are asserted, deliberately: both values are
    ESTIMATED in the xacro (no STS3215 torque figure or lead-screw ratio is
    recorded anywhere in this repo), so pinning either would assert a guess.
    """
    limit = _lift_limit(parsed_model)
    faults = ['%s is %r' % (name, getattr(limit, name, None))
              for name in ('effort', 'velocity')
              if not getattr(limit, name, None) or getattr(limit, name) <= 0]
    assert not faults, (
        'column_lift needs a positive effort and velocity limit: %s. Both are '
        'estimates (see the xacro), but a zero or negative one is a joint no '
        'planner or controller will move.' % faults)


def test_column_lift_can_outrun_the_safety_layers_column_cap(parsed_model):
    """The lift's *capability* is strictly faster than the safety layer's *policy*.

    Two numbers about the same axis that mean different things: the URDF's
    ``<limit velocity>`` is what the mechanism can do (what MoveIt and
    ros2_control plan and clamp against), while ``limits.yaml``'s
    ``velocity.column`` is how fast this robot is *allowed* to move a lift
    through the height where hands are. A clamp only does something if there is
    something above it to clamp; capability equal to policy makes the safety
    layer's column cap vacuous for every consumer that believes the
    description, and capability *below* policy makes the cap unreachable
    instead. Neither shows up anywhere else -- the positive-limits test next
    door is happy with any positive number, and the safety layer's own suite
    never reads the URDF.

    Not hypothetical: this file shipped ``velocity="0.15"`` -- the cap's exact
    value, arrived at independently as an estimate -- for one review round.
    That is the D29 wheel-radius shape (two packages agreeing by coincidence
    with nothing asserting it), pointed at invariant 3.
    """
    velocity = _lift_limit(parsed_model).velocity
    assert velocity > SAFETY_COLUMN_SPEED_CAP_MPS, (
        'the column lift declares a top speed of %r m/s, which does not exceed '
        "the safety layer's column cap of %r m/s "
        '(robot_safety/robot_safety/limits.yaml, velocity.column). The URDF '
        'states capability and the cap states policy: policy at or above '
        'capability is a clamp that can never bind.' % (
            velocity, SAFETY_COLUMN_SPEED_CAP_MPS))


def test_column_lift_axis_is_vertical_in_base_link(parsed_model):
    """The lift travels along ``base_link``'s +z once every rpy above it is composed.

    D29's composed-axis check, applied to the column. A lift that travels
    sideways -- or worse, one whose own ``<axis>`` reads ``0 0 1`` under a
    joint origin that rotates it -- is type-correct, limit-correct, geometry-
    correct and utterly wrong, and nothing else in this file looks at the
    direction: the limits test reads two scalars, the clearance and span tests
    read z extents, and ``check_urdf``/``robot_state_publisher`` are happy with
    a horizontal column. The axis is resolved through *every* joint between the
    lift and the root, not just its own, because that composition is what PR6
    and PR7 will read the column's travel direction off.
    """
    axis = _axis_in_base_link(parsed_model, parsed_model.joint_map['column_lift'])
    assert all(abs(axis[i] - (0.0, 0.0, 1.0)[i]) < PLACEMENT_TOL_M
               for i in range(3)), (
        "column_lift's axis, resolved into base_link, is %s; the column lifts, "
        "so it must be +z (0, 0, 1). Note this composes the joint's own rpy "
        'with every joint above it: a literal `0 0 1` axis under a rotated '
        'origin fails here, correctly.' % ([round(value, 6) for value in axis],))


def test_column_rail_stands_on_the_chassis(parsed_model):
    """The mast is a fixed child of ``base_link`` whose foot clears the chassis puck.

    The column/chassis version of the clearance the base already asserts for
    its wheels, and it exists because that one does not generalise: its second
    half names ``base_chassis_link`` and the wheels explicitly. Root the mast
    at ``base_link``'s own z (axle height, which is where a joint origin with
    no offset puts it) and its lower 115 mm sit inside the chassis solid -- two
    solids in a penetration the model states no relationship about (they are
    siblings), and every other assertion here stays green. That is the exact
    bug D29's red-team round found buried in the base's own wheels.

    Both heights are read off the parsed model and compared as a
    *relationship*, never against the literal that satisfies them today
    (0.115 m), so retuning the chassis, the mast or the carriage keeps the
    constraint enforced rather than breaking the test.

    **"Stands on" is two claims, and the second one had to be added** after a
    review round found this test green for a mast standing five metres from
    the robot: every column assertion in this file read a z, so the mast's
    *height* above the deck was gated and its *position over* the deck was not,
    at any of the four places the column carries a translation. A test whose
    name claims more than it checks is the defect, so the footprint clause
    below is the name being made true, and the general form of the lesson is
    worth more than the instance: ask of each geometric assertion which
    coordinates it silently ignores.

    Scope of the footprint clause, stated because it is conservative in one
    direction and deliberately permissive in another. Conservative: it requires
    the mast's whole footprint inside the puck, so a legitimately cantilevered
    mast overhanging the deck edge would fail it and should relax it to the
    support its own design has, not delete it. Permissive: it is a containment,
    **not a placement** -- it does not say the column is centred, only that it
    stands on the deck, because an off-centre column is a reasonable thing for
    PR4 to want when it hangs two arms off the carriage. If a later PR needs
    the lateral position pinned to a *value* (the way the wheels' angles are
    pinned to the driver's matrix), that is a different, absolute assertion and
    this one does not substitute for it. And it constrains the **mast** only:
    the carriage's own footprint is bounded here only indirectly, through
    ``test_column_carriage_wraps_the_mast``, and nothing says the carriage may
    not overhang the deck -- which is correct for a body that spends its life
    in the air, and worth re-asking when PR4 hangs arms off it.
    """
    rail_joints = [joint for joint in parsed_model.joints
                   if joint.child == 'column_rail_link']
    assert len(rail_joints) == 1, (
        'expected exactly one joint parenting column_rail_link, found %d: '
        '%s' % (len(rail_joints), [joint.name for joint in rail_joints]))
    assert rail_joints[0].type == 'fixed', (
        'the mast is structure, not a mechanism: column_rail_joint must be '
        'fixed, it is %r' % rail_joints[0].type)
    assert rail_joints[0].parent == 'base_link', (
        'the column mounts on the base (D26/#73), so the mast hangs off the '
        'root frame; its parent is %r' % rail_joints[0].parent)

    rail_joint = _axis_aligned_joint(parsed_model, rail_joints[0].name)
    rail_size, rail_offset = _collision_box(parsed_model, 'column_rail_link')
    assert all(dimension > 0 for dimension in rail_size), (
        'the mast collision box is degenerate: %s. A zero-thickness or '
        'zero-length mast satisfies every height relationship in this file '
        'while describing no solid at all.' % (list(rail_size),))
    rail_length = rail_size[2]
    rail_foot = _joint_z(rail_joint) + rail_offset[2] - rail_length / 2.0

    chassis_joint = _axis_aligned_joint(parsed_model, 'base_chassis_joint')
    chassis_radius, chassis_height = _collision_cylinder(
        parsed_model, 'base_chassis_link')
    chassis_top = _joint_z(chassis_joint) + chassis_height / 2.0

    assert rail_foot >= chassis_top - PLACEMENT_TOL_M, (
        "the column sinks into the base: the mast's foot sits at z = %.4f "
        "while the chassis puck's top surface is at z = %.4f. They are in "
        'different subtrees, so the model states no relationship between two '
        "solids that would then overlap; the rail joint's z must be at least "
        'chassis_top + rail_length/2 = %.4f.' % (
            rail_foot, chassis_top, chassis_top + rail_length / 2.0))

    # The mast's footprint, in base_link's x/y: the joint that places the link,
    # plus the shape's own offset within it, plus the half-section. Both frames
    # are known unrotated (the joint by _axis_aligned_joint, the shape by
    # _box_geometry), so the farthest point of an axis-aligned box is its
    # corner -- hypot of the two per-axis extremes, not the larger of them, and
    # not hypot(centre) + half-diagonal, which would reject a legal mast sitting
    # near the rim.
    rail_x = _joint_xyz(rail_joint)[0] + rail_offset[0]
    rail_y = _joint_xyz(rail_joint)[1] + rail_offset[1]
    footprint_reach = math.hypot(abs(rail_x) + rail_size[0] / 2.0,
                                 abs(rail_y) + rail_size[1] / 2.0)
    chassis_x, chassis_y = _joint_xyz(chassis_joint)[:2]
    assert abs(chassis_x) < PLACEMENT_TOL_M and abs(chassis_y) < PLACEMENT_TOL_M, (
        'the chassis puck is displaced from base_link (x=%.4f, y=%.4f), so the '
        "mast's footprint below is measured against the wrong centre. Compose "
        'the offset here rather than deleting the check.' % (chassis_x, chassis_y))
    assert footprint_reach <= chassis_radius + PLACEMENT_TOL_M, (
        'the mast stands off the deck: its collision footprint reaches %.4f m '
        'from base_link (corner of a %.3f x %.3f section centred at x=%.4f, '
        'y=%.4f) while the chassis puck it stands on has radius %.4f m. The '
        'height clause above is green for a mast in the next room -- this is '
        'the half of "stands on" that says *over what*.' % (
            footprint_reach, rail_size[0], rail_size[1], rail_x, rail_y,
            chassis_radius))


def test_column_rail_spans_the_carriage_travel(parsed_model):
    """The mast is long enough for the carriage to stay on it over the whole travel.

    A rail shorter than the travel it permits is a carriage that flies off the
    end of its own mast: physically incoherent, and green under every other
    assertion in this file -- the limits test reads the travel without knowing
    how long the rail is, and the geometry tests know the rail's length without
    knowing what the joint is allowed to do. Nothing else relates the two.

    Deliberately the **strong** form, in the same spirit as the chassis/wheel
    clearance above: it requires the carriage to be fully *contained* by the
    mast at the top of the travel, not merely for the mast to be as long as the
    travel, because a carriage half off the end is supported by nothing. A
    later design where the carriage legitimately overhangs (a short mast with a
    long cantilevered plate) should relax this to the containment its own
    mechanism needs rather than delete it.
    """
    limit = _lift_limit(parsed_model)
    travel = limit.upper - limit.lower
    (_x, _y, rail_length), rail_offset = _collision_box(parsed_model, 'column_rail_link')
    assert rail_length >= travel - PLACEMENT_TOL_M, (
        'the mast is %.4f m long but the carriage is allowed %.4f m of travel '
        '(%.4f to %.4f), so it leaves the rail entirely' % (
            rail_length, travel, limit.lower, limit.upper))

    rail_joint = _axis_aligned_joint(parsed_model, parsed_model.parent_map['column_rail_link'][0])
    rail_centre = _joint_z(rail_joint) + rail_offset[2]
    lift_joint = _axis_aligned_joint(parsed_model, 'column_lift')
    (_cx, _cy, carriage_height), carriage_offset = _collision_box(parsed_model, 'column_top')
    # The datum's height above base_link at the top of the travel, plus the
    # carriage body hanging off it.
    datum_top = _joint_z(rail_joint) + _joint_z(lift_joint) + limit.upper
    carriage_top = datum_top + carriage_offset[2] + carriage_height / 2.0
    carriage_bottom_at_rest = (_joint_z(rail_joint) + _joint_z(lift_joint)
                               + limit.lower + carriage_offset[2]
                               - carriage_height / 2.0)
    assert carriage_top <= rail_centre + rail_length / 2.0 + PLACEMENT_TOL_M, (
        'at its upper limit the carriage reaches z = %.4f while the mast ends '
        'at z = %.4f: the top of the travel hangs off the top of the rail' % (
            carriage_top, rail_centre + rail_length / 2.0))
    assert carriage_bottom_at_rest >= rail_centre - rail_length / 2.0 - PLACEMENT_TOL_M, (
        'at its lower limit the carriage reaches down to z = %.4f while the '
        'mast starts at z = %.4f: the bottom of the travel hangs off the foot '
        'of the rail' % (carriage_bottom_at_rest, rail_centre - rail_length / 2.0))


def test_column_carriage_wraps_the_mast(parsed_model):
    """The carriage's cross-section contains the mast's, so the two really are in contact.

    D31 clause 3 rests its whole justification for parenting the lift to the
    rail on the two solids overlapping by construction -- "a carriage rides its
    mast". Until this assertion existed that was a measurement somebody took
    once and wrote down: moving ``column_lift``'s origin 0.5 m sideways left a
    carriage riding half a metre beside a rail it never touches, with all
    twenty-four other assertions green, and the decision entry silently false
    about the shipped model.

    Composed in the **rail's** frame, which is where the relationship lives:
    the carriage's section centre is the lift joint's own x/y plus the
    carriage shape's offset within its link, and the rail's is its shape's
    offset within the rail link. Both frames are known unrotated
    (``_axis_aligned_joint``, ``_box_geometry``), so containment is a per-axis
    interval comparison.

    Independent of the joint value *because* the travel direction is +z:
    ``test_column_lift_axis_is_vertical_in_base_link`` is what makes that true,
    and this test would be about one configuration rather than all of them if
    that one were deleted -- which is the kind of dependency worth naming
    rather than leaving for a reader to notice.

    Scope: containment is the **strong** form of "wraps", and a real linear
    guide is often a C-profile block that embraces three sides of a rail rather
    than four. Such a design should relax this to the overlap its own section
    has -- the claim being gated is that the two sections *meet*, and full
    containment is simply the cheapest honest way to state it for the box
    primitives this model uses. It says nothing about z (the span and datum
    tests own that) and nothing about where the pair sits in ``base_link`` (the
    footprint clause in ``test_column_rail_stands_on_the_chassis`` owns that).
    """
    lift_joint = _axis_aligned_joint(parsed_model, 'column_lift')
    rail_size, rail_offset = _collision_box(parsed_model, 'column_rail_link')
    carriage_size, carriage_offset = _collision_box(parsed_model, 'column_top')

    lift_xyz = _joint_xyz(lift_joint)
    faults = []
    for axis, name in ((0, 'x'), (1, 'y')):
        rail_centre = rail_offset[axis]
        carriage_centre = lift_xyz[axis] + carriage_offset[axis]
        overhang = (abs(rail_centre - carriage_centre) + rail_size[axis] / 2.0
                    - carriage_size[axis] / 2.0)
        if overhang > PLACEMENT_TOL_M:
            faults.append(
                '%s: the mast spans %.4f..%.4f in the rail frame while the '
                'carriage spans %.4f..%.4f -- %.4f m of mast is outside it' % (
                    name,
                    rail_centre - rail_size[axis] / 2.0,
                    rail_centre + rail_size[axis] / 2.0,
                    carriage_centre - carriage_size[axis] / 2.0,
                    carriage_centre + carriage_size[axis] / 2.0,
                    overhang))
    assert not faults, (
        'the carriage does not wrap the mast: %s. A carriage that misses its '
        'own rail is a lift held up by nothing, and it makes D31 clause 3 -- '
        'which justifies parenting this joint to the rail by the two solids '
        'being in permanent contact -- false about the shipped model, with '
        'every height assertion in this file still green.' % faults)


def test_column_mast_is_drawn_as_it_collides(parsed_model):
    """The mast's ``<visual>`` box is the same box as its ``<collision>``.

    The chassis and the carriage each carry this assertion under the same
    principle -- what a reviewer sees must be what the planner hits -- and the
    mast was the one solid in the description that did not. Both boxes come
    from the same xacro properties today, so divergence takes a deliberate
    edit; that is equally true of the carriage, which is gated, and the reason
    to gate it is that the visual is the *only* part of this model a human
    reviewer actually looks at. A mast drawn slim and colliding fat (or the
    reverse) is a robot whose renders and whose planner disagree about where
    the column is, with nothing else here objecting.
    """
    collision_size, collision_offset = _collision_box(parsed_model, 'column_rail_link')
    visuals = parsed_model.link_map['column_rail_link'].visuals
    assert visuals, 'column_rail_link has no <visual>'
    visual_size, visual_offset = _box_geometry(
        visuals[0], "column_rail_link's <visual>")
    assert (all(abs(visual_size[i] - collision_size[i]) < PLACEMENT_TOL_M
                for i in range(3))
            and all(abs(visual_offset[i] - collision_offset[i]) < PLACEMENT_TOL_M
                    for i in range(3))), (
        'the mast is drawn as a box of %s at %s but collides as %s at %s' % (
            list(visual_size), list(visual_offset),
            list(collision_size), list(collision_offset)))


def test_column_datum_rests_on_the_mast_foot_at_the_lower_limit(parsed_model):
    """At zero travel the mount datum sits exactly one carriage above the mast's foot.

    This is the identity every height claim about this robot is derived from,
    and until it was written down nothing asserted it -- which is precisely how
    the durable docs came to state the datum height with ``column_lift``'s own
    origin left out, overstating it by 585 mm on the one sentence PR6 is told
    to reconcile against ``RobotModel``. Prose drifts from arithmetic that no
    test performs.

    What it pins: the *zero of the travel* is the carriage resting on the
    bottom of the rail, so the datum's height above ``base_link`` at joint
    value q is ``column_rail_joint.z + column_lift.origin.z + q`` -- both
    origins, not just the rail's -- and equals
    ``chassis_top + column_carriage_height + (q - lower)``. The span test next
    door only requires the carriage to be *somewhere* on the rail at both ends,
    so it tolerates sliding the lift joint's origin by as much as the mast's
    over-travel (measured: up to +0.05 m stays green there, +0.06 m does not)
    while every height PR3.5, PR4 and PR6 compute from this datum moves by the
    same amount. This one catches +0.02 m. Read as a relationship between
    numbers off the model, so retuning the chassis, the mast or the carriage
    keeps it.
    """
    limit = _lift_limit(parsed_model)
    rail_joint = _axis_aligned_joint(
        parsed_model, parsed_model.parent_map['column_rail_link'][0])
    lift_joint = _axis_aligned_joint(parsed_model, 'column_lift')
    rail_size, rail_offset = _collision_box(parsed_model, 'column_rail_link')
    carriage_size, _carriage_offset = _collision_box(parsed_model, 'column_top')

    rail_foot = _joint_z(rail_joint) + rail_offset[2] - rail_size[2] / 2.0
    datum_at_rest = _joint_z(rail_joint) + _joint_z(lift_joint) + limit.lower
    expected = rail_foot + carriage_size[2]
    assert abs(datum_at_rest - expected) < PLACEMENT_TOL_M, (
        'at the lower limit (%.4f) the mount datum sits at z = %.4f above '
        "base_link, but the mast's foot is at %.4f and the carriage is %.4f "
        'tall, so a carriage resting on the foot puts the datum at %.4f. '
        'Either the carriage is floating above the bottom of its own travel or '
        'it starts below the mast; both make every height derived from '
        'column_top wrong by the difference.' % (
            limit.lower, datum_at_rest, rail_foot, carriage_size[2], expected))


def test_column_top_is_the_arm_mount_datum(parsed_model):
    """``column_top``'s link frame is the carriage's top surface, not its middle.

    This is the semantics the whole column hangs on and the one thing in it a
    reviewer cannot eyeball. ``column_top`` is both the moving carriage *and*
    the datum PR3.5's head camera and PR4's shoulders are placed against
    (``shoulder_offset_z`` is measured "above ``column_top``" in the roadmap's
    own table), which is only true if the body sits entirely below its own link
    frame. Centre the box on the frame instead -- the obvious way to author a
    link, and what every other solid in this description does -- and the name
    stops being true: everything mounted at the datum starts half-buried inside
    the carriage, which is D29's buried-wheel bug displaced one link up and
    invisible to every other assertion here, since the link set, the inertia
    check and the geometry check never look at *where* a shape sits inside its
    link.

    The visual is required to agree with the collision for the same reason the
    chassis's is: what a reviewer sees must be what the planner hits, and a
    datum that is right in one and wrong in the other is worse than either.
    """
    (size, offset) = _collision_box(parsed_model, 'column_top')
    top_face = offset[2] + size[2] / 2.0
    assert size[2] > 0, 'the carriage collision box is degenerate: %s' % (list(size),)
    assert top_face <= PLACEMENT_TOL_M, (
        "column_top's collision body reaches z = %.4f in its own link frame, "
        'i.e. above the frame origin. That origin is the arm/head mount datum '
        '(the roadmap measures shoulder_offset_z from it), so the carriage '
        'must hang below it -- offset the shape by -height/2 rather than '
        'centring it on the frame.' % top_face)

    visual = parsed_model.link_map['column_top'].visuals
    assert visual, 'column_top has no <visual>'
    visual_size, visual_offset = _box_geometry(visual[0], "column_top's <visual>")
    assert (all(abs(visual_size[i] - size[i]) < PLACEMENT_TOL_M for i in range(3))
            and all(abs(visual_offset[i] - offset[i]) < PLACEMENT_TOL_M
                    for i in range(3))), (
        'the carriage is drawn as a box of %s at %s but collides as %s at %s; '
        'what a reviewer sees must be what the planner hits' % (
            list(visual_size), list(visual_offset), list(size), list(offset)))


def test_solid_links_have_visual_and_collision_geometry(parsed_model):
    """Every link that is a body has geometry, and the body clears the wheels.

    Two clauses, one question -- does this description actually describe a
    *solid* base -- and both are invisible to everything else in this file.
    The link set proves a link exists, ``check_urdf`` and
    ``robot_state_publisher`` are happy with a link that is nothing but a name
    plus an inertial, and the inertia test only reads masses. So the base
    could ship with no visual geometry at all and no body collision, and the
    gate whose stated justification is "geometry is the part a reviewer cannot
    eyeball" (D27/D29) would not notice. That is issue #65's first acceptance
    criterion, so it gets an assertion.

    The second clause is the one a reviewer *really* cannot eyeball: the
    chassis puck and the wheels are **siblings** under ``base_link``, so if the
    puck's underside dips below the top of the wheels, half of every wheel is
    inside the body solid and nothing filters that contact -- RViz draws wheels
    sunk to their axles, MoveIt permanently disables a pair that should be
    checked, and PR7's MJCF starts in penetration. The clearance is asserted as
    a *relationship* between numbers read off the model, not as the literal
    that satisfies it today, so retuning any of the three dimensions keeps the
    constraint enforced (D29).

    That clearance test is deliberately the **strong** form: it requires the
    puck to sit entirely above the wheels and ignores radial separation, so it
    would also reject a legitimate narrow chassis hanging *between* the wheels
    (radius < the wheels' inner band). Conservative is the right direction for
    a gate, but a PR that wants that design should relax this to a real
    cylinder-vs-cylinder test rather than delete it.
    """
    missing = []
    for link in parsed_model.links:
        if link.name in MASSLESS_FRAME_LINKS:
            continue
        if not link.visuals:
            missing.append('%s: no <visual>' % link.name)
        if not link.collisions:
            missing.append('%s: no <collision>' % link.name)
    assert not missing, (
        'link(s) that are bodies rather than frames must carry both visual '
        'and collision geometry: %s' % missing)

    chassis_radius, chassis_height = _collision_cylinder(
        parsed_model, 'base_chassis_link')
    assert chassis_radius > 0 and chassis_height > 0, (
        'the chassis collision cylinder is degenerate: radius %r, length %r' % (
            chassis_radius, chassis_height))
    chassis_visual = parsed_model.link_map['base_chassis_link'].visuals[0].geometry
    assert (abs(getattr(chassis_visual, 'radius', -1) - chassis_radius) < PLACEMENT_TOL_M
            and abs(getattr(chassis_visual, 'length', -1) - chassis_height)
            < PLACEMENT_TOL_M), (
        'the chassis is drawn as %r but collides as a cylinder of radius %.4f '
        'and length %.4f; what a reviewer sees must be what the planner hits' % (
            chassis_visual, chassis_radius, chassis_height))

    chassis_joints = [joint for joint in parsed_model.joints
                      if joint.child == 'base_chassis_link']
    assert len(chassis_joints) == 1, (
        'expected exactly one joint parenting base_chassis_link, found %d: '
        '%s' % (len(chassis_joints),
                [joint.name for joint in chassis_joints]))
    chassis_origin = chassis_joints[0].origin
    assert all(abs(value) < PLACEMENT_TOL_M for value in chassis_origin.rpy), (
        'base_chassis_joint rotates the chassis (rpy=%s). The clearance below '
        'measures the puck along its own +z and compares it to a height in '
        'base_link, which only means anything while the two frames are '
        'aligned; tip the puck on its side and it can swallow the wheels with '
        'the arithmetic still passing.' % (chassis_origin.rpy,))
    chassis_z = chassis_origin.xyz[2]
    wheel_radius = _wheel_radius(parsed_model)
    underside = chassis_z - chassis_height / 2.0
    assert underside >= wheel_radius, (
        'the chassis intersects the wheels: its underside sits at z = %.4f '
        '(centre %.4f minus half of %.4f) while the wheels reach z = %.4f. '
        'They are siblings under base_link, so this contact is not filtered '
        'anywhere; raise chassis_z_offset to at least '
        'wheel_radius + chassis_height/2 = %.4f.' % (
            underside, chassis_z, chassis_height, wheel_radius,
            wheel_radius + chassis_height / 2.0))


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
        if not reader.is_alive():
            # Guarded, because close() takes the buffer lock the reader holds
            # while blocked in read(): closing under a still-blocked reader
            # would hang the suite instead of failing it. Reachable only if a
            # descendant escaped the process group, which is exactly when a
            # hang would be least welcome. Leaking the fd in that case is
            # strictly better -- the process is about to exit anyway.
            process.stdout.close()


def test_every_asset_reference_resolves(expansion, share_dir):
    """Every file the description names -- of any FILE_BEARING_TAGS kind -- exists.

    Still empty after PR2, and deliberately so: the base is primitives and
    vendors no meshes (D29), so this is the same shape as EXPECTED_LINKS --
    costing nothing until the first PR that lands real geometry files, and
    load-bearing from that moment. check_urdf validates the model but never
    opens a mesh or a texture, so without this a typo'd filename, an asset
    committed to src/ but not installed, or one referenced and never committed
    at all is green here and red at bringup.
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
