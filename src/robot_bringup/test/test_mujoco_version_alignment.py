# Copyright (c) 2026 Jaime C.
#
# Use of this source code is governed by an MIT-style
# license that can be found in the LICENSE file or at
# https://opensource.org/licenses/MIT.

"""Version-split regression guard — the ROS sim links MuJoCo 3.12, not 3.9.

The ROS sim (``dfki-ric/mujoco_ros2_control``) source-builds MuJoCo through
CMake ``FetchContent``.  Historically that FetchContent hardcoded the **3.9.0**
tag while the Python/direct path used conda mujoco **3.12.0**, so the two tracks
ran *different* MuJoCo versions (the "3.9/3.12 split").  Issue #139 removed the
split: ``pixi.toml``'s ``build`` task sets
``-DFETCHCONTENT_SOURCE_DIR_MUJOCO=<worktree>/src/mujoco`` (a ``vcs``-imported
MuJoCo 3.12.0 checkout per ``robot.repos``), overriding the vendored
``GIT_TAG 3.9.0`` (see ``docs/design/decisions.md`` D38).

The behavioural bringup test (``test_pr2_navigate.py``) passes on *both* 3.9 and
3.12, so it cannot detect a re-split.  This test pins the **structural** fact
that is the whole point of #139: the installed ``mujoco_ros2_control`` prefix
ships MuJoCo **3.12.0** and NOT 3.9.0.  If someone reverts the override (or the
vendored ``GIT_TAG`` drifts back to 3.9), the shipped lib name flips and this
test fails — without needing a ROS graph, an ELF parse, or a running sim.

Conditional like the other bringup tests: without the source-built
``mujoco_ros2_control`` the sim cannot be checked, so skip (with the build
command) rather than fail on an uninstalled dependency.
"""
import glob
import os

import pytest


def _have_package(pkg):
    """Return True iff ``pkg`` is registered in the ament index."""
    from ament_index_python.packages import PackageNotFoundError
    from ament_index_python.packages import get_package_prefix
    try:
        get_package_prefix(pkg)
    except PackageNotFoundError:
        return False
    return True


def _installed_lib_dir():
    """The installed ``lib/`` of the vendored ``mujoco_ros2_control`` prefix."""
    from ament_index_python.packages import get_package_prefix
    return os.path.join(get_package_prefix('mujoco_ros2_control'), 'lib')


def _shipped_libmujoco_sonames(lib_dir):
    """The MuJoCo shared-lib basenames the prefix ships (``libmujoco.so.*``).

    Matches the unversioned symlink, the dev symlink and the versioned SONAME
    object alike; the SONAME token is what distinguishes 3.9 from 3.12.
    """
    return sorted(
        os.path.basename(p)
        for p in glob.glob(os.path.join(lib_dir, 'libmujoco.so*'))
    )


@pytest.mark.skipif(
    not _have_package('mujoco_ros2_control'),
    reason='mujoco_ros2_control is not installed; run `pixi run build` first.',
)
def test_ros_sim_links_mujoco_312_not_39():
    """The installed ROS-sim prefix ships MuJoCo 3.12.0 and NOT 3.9.0 (#139).

    A re-split to 3.9 flips the shipped lib filename, so the "no 3.9.0" half of
    this assertion is what catches a regression (the "3.12.0 present" half alone
    would also pass on a stale install that still carried both).  Asserting both
    means a stale 3.9 artifact left behind by a dirty install dir also fails,
    which is intended: a clean #139 install ships 3.12.0 only.
    """
    lib_dir = _installed_lib_dir()
    assert os.path.isdir(lib_dir), (
        'mujoco_ros2_control is registered in the ament index but its lib/ '
        'dir is missing (%s) -- a broken/partial install.' % lib_dir)

    shipped = _shipped_libmujoco_sonames(lib_dir)
    assert shipped, (
        'mujoco_ros2_control ships no libmujoco.so* under %s; the sim links '
        'MuJoCo, so this is a broken install (shipped: %r).' % (lib_dir, shipped))

    assert not any('3.9.0' in name for name in shipped), (
        'the ROS sim re-split to MuJoCo 3.9: %s still ships %r. #139 removed '
        'the 3.9/3.12 split by overriding the vendored FetchContent GIT_TAG '
        '3.9.0 with FETCHCONTENT_SOURCE_DIR_MUJOCO (pixi.toml build task -> the '
        'vcs-pinned src/mujoco 3.12.0 checkout). Restore that override.'
        % (lib_dir, shipped))

    assert any('3.12.0' in name for name in shipped), (
        'the ROS sim does not link MuJoCo 3.12.0: %s ships %r (expected a '
        'libmujoco.so.3.12.0). #139 aligned the sim onto the 3.12.0 the '
        'Python path uses, via FETCHCONTENT_SOURCE_DIR_MUJOCO.'
        % (lib_dir, shipped))
