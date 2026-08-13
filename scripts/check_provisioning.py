#!/usr/bin/env python3
# Copyright (c) 2026 Jaime C.
#
# Use of this source code is governed by an MIT-style
# license that can be found in the LICENSE file or at
# https://opensource.org/licenses/MIT.

"""Refuse to start a test run on a worktree that was never provisioned.

A fresh worktree whose ``pixi run install-openclaw`` did not happen -- the
bootstrap in ``scripts/start-feature.sh`` treats it as a soft failure, so one
network hiccup is enough -- turns into **six** ``robot_brain`` failures
several minutes into ``colcon test``. Each one says the right thing (``no
OpenClaw CLI at ...: run `pixi run install-openclaw```), but it says it six
times, at the bottom of a full workspace run, interleaved with everything
else, and it reads like the feature under test broke. The failure should
teach the fix, so this runs *first* (``pixi run test`` ``depends-on`` it) and
says it once, in seconds.

**Hard fail, never a skip.** Turning the OpenClaw tests off on the machine
that has no OpenClaw is the same lie
``src/robot_brain/test/test_openclaw_validates.py`` refuses to tell (see its
"No skip" note) -- and the ratchet in ``scripts/check_test_integrity.py``
counts tests *collected*, so a skip would be silent. The guard states the
condition, the path it looked at, and the one command that fixes it.

The checks are a **list**, deliberately, with one entry today: the next
un-provisioned condition worth catching is an append, not a redesign. It is
one entry rather than three because the other candidates are not real -- there
is no ``pixi run`` at all without ``.pixi/``, and a missing ``build/`` is
already reported per package by the audit.

Usage::

    python scripts/check_provisioning.py
    python scripts/check_provisioning.py --repo-root /path/to/checkout
"""

import argparse
import os
from pathlib import Path
import sys

#: Marks the repository root. A marker walk rather than a fixed number of
#: ``..``s, matching ``test_openclaw_validates.repository_root``.
ROOT_MARKER = 'pixi.toml'

#: Where ``pixi run install-openclaw`` puts the CLI: a project-local npm
#: prefix, gitignored. The same constant lives in
#: ``src/robot_brain/test/test_openclaw_validates.py`` -- this file cannot
#: import it (the guard runs before anything is built, and outside the
#: workspace packages), and a second spelling of one path is the cheapest
#: honest option available here.
OPENCLAW_RELATIVE_PATH = Path('node') / 'node_modules' / '.bin' / 'openclaw'

#: The remediation, on a line of its own so it can be copy-pasted whole.
INSTALL_COMMAND = 'pixi run install-openclaw'


def repository_root(start=None):
    """Return the repository root, found by walking up for ``pixi.toml``."""
    start = Path(__file__ if start is None else start).resolve()
    for directory in [start, *start.parents]:
        if (directory / ROOT_MARKER).is_file():
            return directory
    raise SystemExit(
        f'check_provisioning.py: no {ROOT_MARKER} above {start}: cannot '
        f'locate the repository root')


def check_openclaw_cli(repo_root):
    """Return a complaint if the project-local OpenClaw CLI cannot be run.

    Executability, not mere existence: an interrupted ``npm install`` can
    leave the name behind without the bit, and ``robot_brain`` shells out to
    it rather than importing it.
    """
    binary = repo_root / OPENCLAW_RELATIVE_PATH
    if binary.is_file() and os.access(binary, os.X_OK):
        return None
    if binary.exists():
        state = 'is not executable'
    elif binary.is_symlink():
        # `exists()` follows the link, so a half-cleaned `node_modules` would
        # otherwise be reported as "missing" while `ls` shows the name sitting
        # right there -- ten minutes of confusion for one word.
        state = 'is a broken symlink'
    else:
        state = 'is missing'
    return (f'the OpenClaw CLI {state}:\n'
            f'    {binary}\n'
            f'  Six robot_brain tests run it (test_openclaw_validates.py puts '
            f'the shipped\n'
            f'  config in front of the real `openclaw config validate`), so '
            f'without it the\n'
            f'  run goes red for a reason that has nothing to do with your '
            f'change.')


#: Every provisioning condition checked, in report order.
CHECKS = (check_openclaw_cli,)


def problems(repo_root):
    """Return every check's complaint, skipping the ones that are satisfied.

    Every check runs: a worktree missing two things should be told both,
    rather than being sent round the loop once per missing thing.
    """
    return [complaint for complaint in
            (check(repo_root) for check in CHECKS) if complaint]


def report(found, stream=None):
    """Write the complaints and the remedy to ``stream`` (default stderr).

    ``sys.stderr`` is looked up when the report is written, not when this
    module is imported, so a caller that replaces it -- pytest's ``capsys``,
    or a wrapper collecting the message -- sees the output.
    """
    stream = sys.stderr if stream is None else stream
    print('check_provisioning.py: this worktree is not provisioned, and '
          '`pixi run test`\nwould fail for that reason rather than for '
          'anything you did.\n', file=stream)
    for complaint in found:
        print(f'  {complaint}\n', file=stream)
    print(f'Fix it with:\n\n{INSTALL_COMMAND}\n', file=stream)


def main(argv=None):
    """Check the worktree; return 0 when it is ready to run tests."""
    parser = argparse.ArgumentParser(
        description='Fail fast on an un-provisioned worktree.')
    parser.add_argument(
        '--repo-root', default=None,
        help='the checkout to check (default: the one holding this script)')
    args = parser.parse_args(argv)

    repo_root = (Path(args.repo_root) if args.repo_root
                 else repository_root())
    found = problems(repo_root)
    if not found:
        return 0
    report(found)
    return 1


if __name__ == '__main__':
    sys.exit(main())
