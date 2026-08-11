# Copyright (c) 2026 Jaime C.
#
# Use of this source code is governed by an MIT-style
# license that can be found in the LICENSE file or at
# https://opensource.org/licenses/MIT.

"""Pytest fixtures for the robot_safety tests."""

import pytest
from robot_safety import SafetyLayer
from safety_fixtures import make_limits


@pytest.fixture
def layer() -> SafetyLayer:
    """Return a layer on synthetic limits: column [0, 1] m, 10 N jaws."""
    return SafetyLayer(limits=make_limits())
