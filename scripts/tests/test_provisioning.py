# Copyright (c) 2026 Jaime C.
#
# Use of this source code is governed by an MIT-style
# license that can be found in the LICENSE file or at
# https://opensource.org/licenses/MIT.

"""Tests for the un-provisioned-worktree guard, ``check_provisioning.py``.

The guard's whole value is the *message*: the condition it found, the path it
looked at, and one command that can be copy-pasted. So that is what these
assert -- a guard that merely exits non-zero would leave the reader exactly
where the six ``robot_brain`` failures already left them.

The wiring is asserted too (``pixi.toml``): a guard nothing runs is worth
nothing, and the dependency that runs it is one line somebody could tidy away.
"""

from pathlib import Path
import tomllib

import check_provisioning as guard
import pytest

#: This file is ``<repo>/scripts/tests/test_provisioning.py``.
REPO_ROOT = Path(__file__).resolve().parent.parent.parent


def provisioned_root(tmp_path, mode=0o755):
    """Return a repo root holding an OpenClaw CLI installed with ``mode``."""
    binary = tmp_path / guard.OPENCLAW_RELATIVE_PATH
    binary.parent.mkdir(parents=True)
    binary.write_text('#!/usr/bin/env node\n')
    binary.chmod(mode)
    return tmp_path


def test_a_provisioned_worktree_passes_and_says_nothing(tmp_path, capsys):
    """The baseline: without it, "it fails when it should" proves nothing."""
    rc = guard.main(['--repo-root', str(provisioned_root(tmp_path))])

    captured = capsys.readouterr()
    assert rc == 0
    assert not captured.out and not captured.err


def test_a_missing_cli_names_the_path_and_the_command_to_fix_it(
        tmp_path, capsys):
    """The remediation is a whole line, so it can be copy-pasted as one.

    And the *resolved* path is printed, not the relative one: on a fresh
    worktree the interesting question is usually "which checkout did it look
    in", and ``node/`` is per-worktree.
    """
    rc = guard.main(['--repo-root', str(tmp_path)])

    err = capsys.readouterr().err
    assert rc == 1
    assert str(tmp_path / guard.OPENCLAW_RELATIVE_PATH) in err
    assert 'pixi run install-openclaw' in [line.strip() for line in
                                           err.splitlines()]
    assert 'is missing' in err


def test_a_cli_that_cannot_be_executed_is_not_provisioned(tmp_path, capsys):
    """An interrupted npm install leaves the name without the bit.

    ``robot_brain`` runs the binary rather than importing it, so a file that
    is there and not runnable fails the suite exactly like an absent one --
    and would be missed by a check that only asked whether the path exists.
    """
    rc = guard.main(['--repo-root', str(provisioned_root(tmp_path, 0o644))])

    err = capsys.readouterr().err
    assert rc == 1
    assert 'is not executable' in err


def test_every_check_reports_even_when_an_earlier_one_failed(
        tmp_path, capsys, monkeypatch):
    """Two missing things is one trip round the loop, not two.

    The checks are a list so the next condition is an append; this pins the
    part that makes a list worth having -- it aggregates rather than
    short-circuiting.
    """
    monkeypatch.setattr(
        guard, 'CHECKS', (lambda root: 'no widget', lambda root: 'no sprocket'))

    rc = guard.main(['--repo-root', str(tmp_path)])

    err = capsys.readouterr().err
    assert rc == 1
    assert 'no widget' in err and 'no sprocket' in err


def test_a_dangling_symlink_is_named_as_one(tmp_path, capsys):
    """Saying "is missing" would be a falsehood about a path ``ls`` shows.

    A half-cleaned ``node_modules`` leaves the ``.bin`` symlink pointing at a
    file that is gone.  The remedy is the same command either way, so the only
    thing at stake is whether the message sends the reader looking for the
    right thing.
    """
    binary = tmp_path / guard.OPENCLAW_RELATIVE_PATH
    binary.parent.mkdir(parents=True)
    binary.symlink_to(tmp_path / 'openclaw' / 'openclaw.mjs')

    rc = guard.main(['--repo-root', str(tmp_path)])

    err = capsys.readouterr().err
    assert rc == 1
    assert 'is a broken symlink' in err


def test_the_guard_finds_this_repository_by_its_marker(monkeypatch):
    """Run with no arguments it must check *this* checkout, not a parent.

    ``pixi run test`` invokes it from the repo root, but a marker walk is what
    keeps it right when it is invoked from anywhere else -- and every worktree
    is a sibling directory holding its own ``node/``, so landing one level up
    would check somebody else's install.
    """
    assert guard.repository_root() == REPO_ROOT
    assert guard.repository_root(REPO_ROOT / 'src' / 'robot_mcp') == REPO_ROOT


def test_the_test_task_runs_the_guard_before_the_suite():
    """The wiring, in the file that decides it.

    ``pixi run test`` aborts and propagates the exit code when a ``depends-on``
    task fails, so this dependency *is* the fail-fast. ``test-audit`` must not
    grow it: re-reading XML that already exists needs no OpenClaw.
    """
    with (REPO_ROOT / 'pixi.toml').open('rb') as handle:
        tasks = tomllib.load(handle)['tasks']

    assert tasks['check-provisioning'].endswith('check_provisioning.py')
    assert tasks['test']['depends-on'] == ['check-provisioning']
    assert isinstance(tasks['test-audit'], str), (
        'test-audit gained a dependency it does not need')


def test_a_tree_with_no_marker_says_so_instead_of_guessing(tmp_path):
    """No ``pixi.toml`` anywhere above: a stated failure, not a wrong answer."""
    with pytest.raises(SystemExit) as raised:
        guard.repository_root(tmp_path)

    assert 'cannot locate the repository root' in str(raised.value)
