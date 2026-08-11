# Copyright (c) 2026 Jaime C.
#
# Use of this source code is governed by an MIT-style
# license that can be found in the LICENSE file or at
# https://opensource.org/licenses/MIT.

"""Pytest fixtures for the robot_mcp tests."""

import pytest
from robot_backends import MockBackend


@pytest.fixture
def anyio_backend() -> str:
    """Run every ``@pytest.mark.anyio`` test on asyncio only (no trio here)."""
    return 'asyncio'


@pytest.fixture
def backend() -> MockBackend:
    """Return the Mock backend the server under test will drive."""
    return MockBackend()


@pytest.fixture
def reference() -> MockBackend:
    """Return a second, identically seeded backend to compare answers against.

    Both are seeded from ``default_world()`` and the Mock is deterministic, so
    replaying the same skills on this one reproduces exactly what the server's
    backend should have produced -- which is what the tool results are asserted
    against, rather than against a hand-copied expected dict.
    """
    return MockBackend()
