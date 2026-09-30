# Copyright (c) 2026 Jaime C.
#
# Use of this source code is governed by an MIT-style
# license that can be found in the LICENSE file or at
# https://opensource.org/licenses/MIT.

"""The hand-authored SRDF says exactly what R2/R3/R4 say it says (R10 item 1).

Three claims, all cheap and all graph-free:

* ``test_srdf_groups_and_end_effectors_match_rulings`` -- parse the installed
  ``config/sisyphus.srdf`` with ``srdfdom`` and assert the group names, chain
  endpoints and end-effector wiring against the rulings.
* ``test_group_joint_sets_are_the_five_arm_joints`` -- walk each chain in the
  *expanded URDF* and assert the exact joint sets: five revolute arm joints
  plus the fixed gripper mount for an arm group, the single driven jaw for a
  gripper group, and ``*_gripper_mirror`` in neither (R3). This proves what
  MoveIt will actually see, not merely what the SRDF declares.
* ``test_srdf_names_all_exist_in_the_expanded_urdf`` -- the drift guard:
  every link/joint the SRDF names exists in the xacro-expanded URDF, so a
  renamed macro parameter fails here rather than when ``move_group`` starts.

The mimic joint's *absence* from every group is asserted explicitly: it is the
one joint whose presence would be a silent correctness bug (MoveIt would plan a
DOF the URDF's ``<mimic>`` immediately overwrites), and the test also asserts
the mimic joint really does exist in the URDF, so the exclusion is meaningful
rather than vacuous.
"""
import os
import xml.etree.ElementTree as ET

#: The five revolute arm joints, per side, in chain order (R2).
ARM_JOINTS = [
    'shoulder_pan', 'shoulder_lift', 'elbow_flex', 'wrist_flex', 'wrist_roll',
]
SIDES = ['left', 'right']

#: The wrist-roll output spool the gripper mounts on; the arm chain's tip.
GRIPPER_MOUNT = 'gripper_base_link'


def _install_share(pkg):
    from ament_index_python.packages import get_package_share_directory
    return get_package_share_directory(pkg)


def _srdf_path():
    return os.path.join(_install_share('robot_moveit_config'),
                        'config', 'sisyphus.srdf')


def _read_srdf_text():
    with open(_srdf_path()) as handle:
        return handle.read()


def _parse_srdf():
    from srdfdom.srdf import SRDF
    return SRDF.from_xml_string(_read_srdf_text())


def _urdf_root():
    """Expand the shipped top-level xacro and return its XML root."""
    import xacro
    path = os.path.join(_install_share('robot_description'),
                        'urdf', 'robot.urdf.xacro')
    return ET.fromstring(xacro.process_file(path).toxml().encode('utf-8'))


def _joints_to_parent_links(urdf):
    """Return ``child_link -> (joint_name, parent_link)`` for the URDF.

    A child link can be claimed by more than one joint (e.g. a jaw link is the
    child of *both* its revolute jaw joint and, via the mirror joint, nothing --
    so here we keep the first joint that names the link as child, which is the
    URDF's declaration order and therefore the chain joint).
    """
    mapping = {}
    for joint in urdf.findall('joint'):
        child = joint.find('child').get('link')
        mapping.setdefault(child, (joint.get('name'),
                                   joint.find('parent').get('link')))
    return mapping


def _chain_joints(urdf, base, tip):
    """Return the joint names from ``base`` down to ``tip``, in order."""
    to_parent = _joints_to_parent_links(urdf)
    chain = []
    link = tip
    while link != base:
        joint, parent = to_parent[link]
        chain.append(joint)
        link = parent
    return list(reversed(chain))


def test_srdf_groups_and_end_effectors_match_rulings():
    """Group names, chain endpoints, end-effectors, and no passive joints."""
    srdf = _parse_srdf()
    assert srdf.name == 'sisyphus', (
        'SRDF robot name must match <robot name="sisyphus"> in the URDF; got %r'
        % srdf.name)

    groups = {g.name: g for g in srdf.groups}
    assert set(groups) == {
        'left_arm', 'left_gripper', 'right_arm', 'right_gripper'}, sorted(groups)

    for side in SIDES:
        arm = groups['%s_arm' % side]
        assert [(c.base_link, c.tip_link) for c in arm.chains] == [
            ('%s_shoulder_link' % side, '%s_%s' % (side, GRIPPER_MOUNT))]

        gripper = groups['%s_gripper' % side]
        assert [(c.base_link, c.tip_link) for c in gripper.chains] == [
            ('%s_%s' % (side, GRIPPER_MOUNT),
             '%s_gripper_upper_jaw_link' % side)]

    eefs = {e.name: e for e in srdf.end_effectors}
    assert set(eefs) == {'left_eef', 'right_eef'}, sorted(eefs)
    for side in SIDES:
        eef = eefs['%s_eef' % side]
        assert eef.parent_link == '%s_%s' % (side, GRIPPER_MOUNT), eef.parent_link
        assert eef.parent_group == '%s_arm' % side, eef.parent_group
        assert eef.group == '%s_gripper' % side, eef.group

    # R3: no group pulls in a subgroup or an explicit joint list, and the SRDF
    # declares no passive joint (the mimic is propagated by MoveIt from URDF).
    for group in srdf.groups:
        assert group.subgroups == [], group.name
        assert group.joints == [], group.name
    assert srdf.passive_joints == []
    assert srdf.virtual_joints == []


def test_group_joint_sets_are_the_five_arm_joints():
    """Each chain expands to exactly the expected joint set (R2/R3).

    Walks the *expanded URDF* from each chain endpoint, so this is what MoveIt
    will see: the arm chain is five revolute joints plus the one fixed gripper
    mount, and the gripper chain is the single driven jaw. ``*_gripper_mirror``
    must be absent from both, and the test additionally asserts the mimic joint
    exists in the URDF (so the exclusion is not vacuous).
    """
    urdf = _urdf_root()
    joint_types = {j.get('name'): j.get('type') for j in urdf.findall('joint')}

    for side in SIDES:
        arm = _chain_joints(urdf, '%s_shoulder_link' % side,
                            '%s_%s' % (side, GRIPPER_MOUNT))
        revolute = [j for j in arm if joint_types[j] == 'revolute']
        assert revolute == ['%s_%s' % (side, n) for n in ARM_JOINTS], (
            'arm chain revolute joints drifted from R2: %r' % (arm,))
        assert '%s_gripper_mount_joint' % side in arm, arm
        assert '%s_gripper_mirror' % side not in arm

        jaw = _chain_joints(urdf, '%s_%s' % (side, GRIPPER_MOUNT),
                            '%s_gripper_upper_jaw_link' % side)
        assert jaw == ['%s_gripper' % side], jaw

        # The mirror jaw is a real URDF joint and IS a mimic: the exclusion
        # above is about a joint that exists, not one renamed away.
        mirror = urdf.find("joint[@name='%s_gripper_mirror']" % side)
        assert mirror is not None
        assert mirror.find('mimic') is not None
        assert mirror.find('mimic').get('joint') == '%s_gripper' % side


def test_srdf_names_all_exist_in_the_expanded_urdf():
    """Every link/joint the SRDF names exists in the expanded URDF.

    The drift guard: the SRDF is hand-written against the xacro's ``${name}``
    naming, so a rename in ``arm.xacro``/``gripper.xacro`` must fail here rather
    than when ``move_group`` refuses to load the model at runtime.
    """
    srdf = _parse_srdf()
    urdf = _urdf_root()
    links = {link.get('name') for link in urdf.findall('link')}
    joints = {joint.get('name') for joint in urdf.findall('joint')}

    for group in srdf.groups:
        for chain in group.chains:
            assert chain.base_link in links, chain.base_link
            assert chain.tip_link in links, chain.tip_link
            for joint in _chain_joints(urdf, chain.base_link, chain.tip_link):
                assert joint in joints, joint
    for eef in srdf.end_effectors:
        assert eef.parent_link in links, eef.parent_link
