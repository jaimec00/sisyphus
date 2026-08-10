# Copyright (c) 2026 Jaime C.
#
# Use of this source code is governed by an MIT-style
# license that can be found in the LICENSE file or at
# https://opensource.org/licenses/MIT.

"""Hold ``scripts/`` to the same linters the ROS packages are held to.

The per-package ``test_flake8.py`` / ``test_copyright.py`` / ``test_pep257.py``
tests only ever see their own package, so workspace tooling outside ``src/``
would otherwise be unlinted. ``ament_copyright`` only inspects Python sources,
so the shell scripts in this directory are out of its reach.
"""

from pathlib import Path

from ament_copyright.main import main as copyright_main
from ament_flake8.main import main_with_errors
from ament_pep257.main import main as pep257_main
import pytest

SCRIPTS_DIR = str(Path(__file__).resolve().parent.parent)


@pytest.mark.flake8
@pytest.mark.linter
def test_flake8():
    """Assert the workspace tooling has no flake8 errors."""
    rc, errors = main_with_errors(argv=[SCRIPTS_DIR])
    assert rc == 0, '\n'.join(
        ['Found %d code style errors / warnings:' % len(errors)] + errors)


@pytest.mark.copyright
@pytest.mark.linter
def test_copyright():
    """Assert every tooling source file carries a copyright header."""
    rc = copyright_main(argv=[SCRIPTS_DIR])
    assert rc == 0, 'Found errors'


@pytest.mark.linter
@pytest.mark.pep257
def test_pep257():
    """Assert the workspace tooling has no docstring style errors."""
    rc = pep257_main(argv=[SCRIPTS_DIR, '--add-ignore', 'D213'])
    assert rc == 0, 'Found code style errors / warnings'
