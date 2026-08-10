# Copyright (c) 2026 Jaime C.
#
# Use of this source code is governed by an MIT-style
# license that can be found in the LICENSE file or at
# https://opensource.org/licenses/MIT.

"""Tests for the seed-world description and its validation."""

from dataclasses import FrozenInstanceError

import pytest
from robot_backends import default_world, MockBackend, MockWorld, ObjectSpec, RobotModel
from robot_skills import NavigateTo, Point, Pose, Side


def test_default_world_supports_the_briefs_scenario():
    """The shipped world has kitchen, table and a graspable mug_1."""
    world = default_world()
    assert {'kitchen', 'table'} <= set(world.locations)
    mug = next(spec for spec in world.objects if spec.object_id == 'mug_1')
    assert mug.graspable is True
    assert world.start_location in world.locations
    assert world.start_pose == world.locations[world.start_location]


def test_world_is_immutable_and_defensively_copied():
    """A backend cannot be reconfigured behind its own back."""
    locations = {'dock': Pose.from_xyz(0.0, 0.0, 0.0)}
    world = MockWorld(locations=locations, start_location='dock')

    locations['kitchen'] = Pose.from_xyz(2.0, 0.0, 0.0)
    assert 'kitchen' not in world.locations

    with pytest.raises(TypeError):
        world.locations['kitchen'] = Pose()
    with pytest.raises(FrozenInstanceError):
        world.start_location = 'kitchen'

    backend = MockBackend(world)
    assert backend.execute(NavigateTo('kitchen')).succeeded is False


def test_world_validation():
    """A malformed world is rejected where it is written, not mid-episode."""
    good = {'dock': Pose.from_xyz(0.0, 0.0, 0.0)}
    with pytest.raises(ValueError, match='must not be empty'):
        MockWorld(locations={}, start_location='dock')
    with pytest.raises(ValueError, match='not one of the locations'):
        MockWorld(locations=good, start_location='kitchen')
    with pytest.raises(TypeError, match='must be a Pose'):
        MockWorld(locations={'dock': (0.0, 0.0, 0.0)}, start_location='dock')
    with pytest.raises(ValueError, match='duplicate object_id'):
        MockWorld(
            locations=good,
            start_location='dock',
            objects=(
                ObjectSpec('cube_1', 'cube', Pose()),
                ObjectSpec('cube_1', 'block', Pose()),
            ),
        )
    with pytest.raises(TypeError, match='must contain ObjectSpec'):
        MockWorld(locations=good, start_location='dock', objects=({'id': 'cube_1'},))
    with pytest.raises(ValueError, match='outside the column range'):
        MockWorld(locations=good, start_location='dock', start_column_height=5.0)


def test_object_spec_validation():
    """Objects need a real id, label and pose."""
    with pytest.raises(ValueError):
        ObjectSpec('', 'cube', Pose())
    with pytest.raises(TypeError):
        ObjectSpec('cube_1', 'cube', (0.0, 0.0, 0.0))
    with pytest.raises(TypeError):
        ObjectSpec('cube_1', 'cube', Pose(), graspable='yes')


def test_robot_model_validation():
    """The kinematic stand-in refuses impossible geometry."""
    with pytest.raises(ValueError, match='reach_radius must be positive'):
        RobotModel(reach_radius=0.0)
    with pytest.raises(ValueError, match='must not exceed max_column_height'):
        RobotModel(min_column_height=1.0, max_column_height=0.5)
    with pytest.raises(ValueError, match='within reach_radius'):
        RobotModel(reach_radius=0.1, home_gripper_offset=Point(1.0, 0.0, 0.0))


def test_robot_model_shoulder_geometry():
    """Shoulders sit either side of the base and ride the lift column."""
    model = RobotModel()
    base = Pose.from_xyz(1.0, 1.0, 0.0)
    left = model.shoulder(base, 0.4, Side.LEFT)
    right = model.shoulder(base, 0.4, Side.RIGHT)
    assert left.y > right.y
    assert left.z == right.z == pytest.approx(0.4 + model.shoulder_offset_z)
    raised = model.shoulder(base, 0.9, Side.LEFT)
    assert raised.z - left.z == pytest.approx(0.5)


def test_backend_exposes_the_world_it_was_seeded_from(backend):
    """The world is queryable, so callers need not guess its contents."""
    assert backend.world.start_location == 'charger'
    assert set(backend.get_observation().known_locations) == set(backend.world.locations)
