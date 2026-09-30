# Copyright (c) 2026 Jaime C.
#
# Use of this source code is governed by an MIT-style
# license that can be found in the LICENSE file or at
# https://opensource.org/licenses/MIT.

"""Run the ament_flake8 linter over this package (exercises the test_depend)."""

from ament_flake8.main import main_with_errors
import pytest


@pytest.mark.flake8
@pytest.mark.linter
def test_flake8():
    """Assert the package has no flake8 errors."""
    rc, errors = main_with_errors(argv=[])
    assert rc == 0, '\n'.join(['Found %d code style errors / warnings:' % len(errors)] + errors)
