# Copyright (c) 2026 Jaime C.
#
# Use of this source code is governed by an MIT-style
# license that can be found in the LICENSE file or at
# https://opensource.org/licenses/MIT.

"""Pytest fixtures for the robot_world tests."""

import pytest
from robot_skills import Pose
from robot_world import WorldDocument, WorldObject, write_document

#: A tiny scene, small enough to assert on whole -- the shipped demo apartment
#: is pinned separately in ``test_default_seed.py``.
SMALL_WORLD = WorldDocument(
    locations={
        'dock': Pose.from_xyz(0.0, 0.0, 0.0),
        'bench': Pose.from_xyz(1.0, 0.0, 0.0),
    },
    start_location='dock',
    objects=(
        WorldObject('cube_1', 'cube', Pose.from_xyz(1.0, 0.1, 0.8)),
        WorldObject('anvil_1', 'anvil', Pose.from_xyz(1.0, -0.1, 0.8), graspable=False),
    ),
    start_column_height=0.4,
)


@pytest.fixture
def document() -> WorldDocument:
    """Return the small two-object scene the store tests mutate."""
    return SMALL_WORLD


@pytest.fixture
def seed_file(tmp_path, document) -> str:
    """Write ``document`` to a seed file and return its path."""
    path = tmp_path / 'seed.json'
    write_document(path, document)
    return str(path)
