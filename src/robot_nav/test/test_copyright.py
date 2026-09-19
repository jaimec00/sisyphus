# Copyright (c) 2026 Jaime C.
#
# Use of this source code is governed by an MIT-style
# license that can be found in the LICENSE file or at
# https://opensource.org/licenses/MIT.

"""Run the ament_copyright linter over this package (exercises the test_depend)."""

from ament_copyright.main import main
import pytest


@pytest.mark.copyright
@pytest.mark.linter
def test_copyright():
    """Assert every source file carries a copyright and license header."""
    rc = main(argv=[])
    assert rc == 0, 'Found errors'
