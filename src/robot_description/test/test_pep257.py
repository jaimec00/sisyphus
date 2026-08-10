# Copyright (c) 2026 Jaime C.
#
# Use of this source code is governed by an MIT-style
# license that can be found in the LICENSE file or at
# https://opensource.org/licenses/MIT.

"""Run the ament_pep257 docstring linter over this package.

D213 ("summary should start at the second line") is excluded: it is mutually
exclusive with D212 ("summary should start at the first line"), which
``ament_flake8``'s own configuration selects by ignoring D212's counterpart.
This repo writes the summary on the first line, as PEP 257 does.
"""

from ament_pep257.main import main
import pytest


@pytest.mark.linter
@pytest.mark.pep257
def test_pep257():
    """Assert the package has no docstring style errors."""
    rc = main(argv=['.', 'test', '--add-ignore', 'D213'])
    assert rc == 0, 'Found code style errors / warnings'
