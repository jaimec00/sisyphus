# Copyright (c) 2026 Jaime C.
#
# Use of this source code is governed by an MIT-style
# license that can be found in the LICENSE file or at
# https://opensource.org/licenses/MIT.

"""Pytest fixtures for the robot_skills tests (helpers live in the fixtures module)."""

import pytest
from robot_skills import Observation
from skill_api_fixtures import assert_round_trip, make_observation


@pytest.fixture
def round_trip():
    """Return the dict/JSON round-trip assertion helper."""
    return assert_round_trip


@pytest.fixture
def observation() -> Observation:
    """Return a representative observation with one graspable object."""
    return make_observation()
