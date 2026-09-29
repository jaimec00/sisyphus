# Copyright (c) 2026 Jaime C.
#
# Use of this source code is governed by an MIT-style
# license that can be found in the LICENSE file or at
# https://opensource.org/licenses/MIT.

"""Pure unit tests for the semantic navigation bridge's graph-free half (R4).

``semantic_nav.py`` wraps rclpy, but the two rules with real content in them --
"copy a ``robot_skills.Pose`` into a ``geometry_msgs/Pose`` field by field" and
"look a location name up in the world document" -- are deliberately pure
functions, unit-testable with no graph, the same split
``omni_base_controller.body_to_wheel`` and
``static_map.occupancy_grid_from_document`` use.

The pose copy is the one that silently ruins a drive if it is wrong: swapping
``z`` and ``w`` (or ``y`` and ``z``) turns a yaw into a pitch, and the base then
drives to a mirrored pose with no error anywhere.  So the test pins each of the
seven components to a *distinct* value -- a copy that dropped or reordered one
cannot pass by symmetry.

The resolution test pins R2: a known name yields the document's pose, an unknown
one yields ``None``, and the refusal names every location (mirroring the store's
wording).
"""

from geometry_msgs.msg import Pose as RosPose
from robot_nav.semantic_nav import (
    pose_from_robot_pose,
    resolve_location,
    unknown_location_error,
)
from robot_skills import Point, Pose, Quaternion
from robot_world import WorldDocument


def _distinct_pose():
    """Return a Pose whose seven components are all distinct (no symmetry pass)."""
    return Pose(
        position=Point(x=1.5, y=-2.25, z=0.75),
        orientation=Quaternion(x=0.1, y=0.2, z=0.3, w=0.9),
    )


def test_pose_from_robot_pose_copies_every_component():
    """Each position/orientation component lands in its own field, unreordered."""
    message = pose_from_robot_pose(_distinct_pose())

    assert isinstance(message, RosPose)
    assert message.position.x == 1.5
    assert message.position.y == -2.25
    assert message.position.z == 0.75
    assert message.orientation.x == 0.1
    assert message.orientation.y == 0.2
    assert message.orientation.z == 0.3
    assert message.orientation.w == 0.9


def test_pose_from_robot_pose_keeps_the_quaternion_convention():
    """A pure yaw about ``z`` stays a yaw (a ``z``/``w`` swap would fail this)."""
    pose = Pose(
        position=Point(0.0, 0.0, 0.0),
        orientation=Quaternion(x=0.0, y=0.0, z=0.7071067811865476, w=0.7071067811865476),
    )
    message = pose_from_robot_pose(pose)
    assert message.orientation.z == 0.7071067811865476
    assert message.orientation.w == 0.7071067811865476
    assert message.orientation.x == 0.0
    assert message.orientation.y == 0.0


def test_pose_from_robot_pose_is_the_identity_for_an_identity_pose():
    """The identity pose copies to the identity message (the common case)."""
    message = pose_from_robot_pose(Pose())
    assert (message.position.x, message.position.y, message.position.z) == (0.0, 0.0, 0.0)
    assert message.orientation.w == 1.0
    assert message.orientation.x == 0.0
    assert message.orientation.y == 0.0
    assert message.orientation.z == 0.0


def test_resolve_location_finds_a_known_location():
    """A known name resolves to the document's own pose."""
    document = WorldDocument(
        locations={'kitchen': Pose.from_xyz(2.0, 0.0, 0.0)},
        start_location='kitchen',
    )
    assert resolve_location(document, 'kitchen') == Pose.from_xyz(2.0, 0.0, 0.0)


def test_resolve_location_returns_none_for_an_unknown_location():
    """An unknown name resolves to ``None`` (the caller builds the refusal)."""
    document = WorldDocument(
        locations={'kitchen': Pose.from_xyz(2.0, 0.0, 0.0)},
        start_location='kitchen',
    )
    assert resolve_location(document, 'attic') is None


def test_unknown_location_error_mirrors_the_store_wording():
    """The refusal names the location and lists the known ones, sorted."""
    document = WorldDocument(
        locations={
            'table': Pose.from_xyz(0.0, 2.0, 0.0),
            'charger': Pose.from_xyz(0.0, 0.0, 0.0),
            'kitchen': Pose.from_xyz(2.0, 0.0, 0.0),
        },
        start_location='charger',
    )
    message = unknown_location_error('attic', document)
    assert message == "unknown location 'attic'; known locations: charger, kitchen, table"


def test_the_shipped_world_resolves_kitchen():
    """Against the real seed scene, ``kitchen`` is the reference pose PR3 drives to."""
    from robot_world import default_seed_document

    document = default_seed_document()
    pose = resolve_location(document, 'kitchen')
    assert pose is not None
    assert pose.position.x == 2.0
    assert pose.position.y == 0.0
    # The message the bridge would send keeps the identity orientation.
    message = pose_from_robot_pose(pose)
    assert message.orientation.w == 1.0
