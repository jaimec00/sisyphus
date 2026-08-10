# Copyright (c) 2026 Jaime C.
#
# Use of this source code is governed by an MIT-style
# license that can be found in the LICENSE file or at
# https://opensource.org/licenses/MIT.

"""Pytest fixtures for the robot_backends tests."""

import pytest
from robot_backends import MockBackend, MockWorld


@pytest.fixture
def backend() -> MockBackend:
    """Return a Mock backend seeded with the default demo world."""
    return MockBackend()


@pytest.fixture
def world(backend: MockBackend) -> MockWorld:
    """Return the immutable world the default backend was seeded from."""
    return backend.world
