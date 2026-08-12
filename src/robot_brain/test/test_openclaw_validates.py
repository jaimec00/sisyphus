# Copyright (c) 2026 Jaime C.
#
# Use of this source code is governed by an MIT-style
# license that can be found in the LICENSE file or at
# https://opensource.org/licenses/MIT.

"""The shipped fragment, put in front of the real ``openclaw config validate``.

The fragment used to be written from OpenClaw's *documentation* and checked
only against a hand-copied list of field names.  It was wrong in two ways at
once (#52): ``agents`` wanted a ``list`` array, not an ``entries`` map, and
``sandbox.mode`` had no ``"read-only"`` in its enum.  Both were invisible to a
Python test that only read the file back.  Since #51 the CLI lives in the pixi
env, so the schema question can be asked of the thing that owns the answer.

**What this proves:** *this* build of OpenClaw parses and accepts *this* file.
The validator is strict and cross-referential -- unknown keys are rejected at
every level, and a ``bindings[].agentId`` naming an absent agent is an error --
so passing is a real statement.

**What it does not prove**, and what ``test_openclaw_config.py`` covers
instead: that the values *mean* what we want.  ``tools.allow`` is
``array<string>`` in the schema, so a glob that matches no tool in existence
validates happily.  Nor does it prove anything about the Pi: whether the
operator's build agrees, whether the SSH leg works, whether the Telegram
account exists.  Those stay manual (see the README).

**No skip.**  If the binary is missing, this fails.  A drift guard that quietly
turns itself off on the machine least likely to have caught the drift by hand
is the same lie as the docstring #52 deleted -- and the ratchet in
``scripts/check_test_integrity.py`` counts tests *collected*, so a skip here
would be silent.  The remedy is one documented command and it is in the
failure message.
"""

from functools import lru_cache
from importlib import resources
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile

import pytest
import robot_brain
from robot_brain.agent import CONFIG_RESOURCE

#: Marks the repository root.  A marker walk rather than a fixed number of
#: ``..``s so that moving the package cannot silently point this somewhere
#: plausible-but-wrong.
ROOT_MARKER = 'pixi.toml'

#: Where ``pixi run install-openclaw`` puts the CLI (a project-local npm
#: prefix, gitignored).  Not ``pixi run openclaw``: that task ``depends-on``
#: ``install-openclaw``, which would put npm and a network round-trip inside
#: every test run.
OPENCLAW_RELATIVE_PATH = Path('node') / 'node_modules' / '.bin' / 'openclaw'

INSTALL_HINT = 'run `pixi run install-openclaw` (project-local, gitignored)'


def repository_root() -> Path:
    """Return the repository root, found by walking up for ``pixi.toml``.

    ``colcon build --symlink-install`` imports this package through
    ``build/robot_brain/robot_brain -> src/robot_brain/robot_brain``, so
    ``robot_brain.__file__`` is a path under ``build/``.  ``.resolve()`` asks
    the kernel and lands in ``src/``; a lexical walk would not.  Counting
    parents from either would give a different answer (``build`` vs ``src``) --
    the marker is what makes the two agree, and ``.resolve()`` is what keeps
    the answer right if the package is ever installed somewhere less tidy.
    """
    start = Path(robot_brain.__file__).resolve()
    for directory in start.parents:
        if (directory / ROOT_MARKER).is_file():
            return directory
    raise AssertionError(
        f'no {ROOT_MARKER} above {start}: cannot locate the repository root, '
        f'so cannot find the OpenClaw CLI')


def openclaw_binary() -> Path:
    """Return the path to the project-local ``openclaw``, or fail saying how."""
    binary = repository_root() / OPENCLAW_RELATIVE_PATH
    assert binary.is_file(), f'no OpenClaw CLI at {binary}: {INSTALL_HINT}'
    return binary


def shipped_fragment() -> Path:
    """Return the path of the fragment as it is installed, not a copy of it."""
    resource = resources.files('robot_brain') / 'openclaw' / CONFIG_RESOURCE
    path = Path(str(resource))
    assert path.is_file(), path
    return path


def scratch_environment(home: Path, config: Path) -> dict:
    """Return a child environment that can only touch ``home``.

    Every ``OPENCLAW_*`` variable is *removed* rather than the two or three
    known dangerous ones being overwritten: ``OPENCLAW_HOME`` beats ``HOME``
    outright (verified -- with both set, the state DB appears under
    ``$OPENCLAW_HOME/.openclaw/state``), ``OPENCLAW_PROFILE`` namespaces state
    paths, and the installed CLI reads 485 distinct ``OPENCLAW_*`` names.  An
    allowlist of things to clobber cannot be complete against that; a denylist
    of the whole prefix can.

    ``PATH`` and the rest are inherited on purpose -- the CLI is a
    ``#!/usr/bin/env node`` shim, and ``node`` is on ``PATH`` only inside the
    pixi env this suite already runs in.
    """
    environment = {key: value for key, value in os.environ.items()
                   if not key.startswith('OPENCLAW_')}
    environment.update(
        HOME=str(home),
        OPENCLAW_STATE_DIR=str(home / 'state'),
        OPENCLAW_CONFIG_PATH=str(config),
    )
    return environment


def validate(config: Path, home: Path) -> subprocess.CompletedProcess:
    """Run ``openclaw config validate`` on ``config``, writing only under ``home``.

    ``config validate`` does not rewrite the config, but it does open a state
    database and npm's cache; both are redirected so a test run leaves nothing
    in the developer's ``~/.openclaw``.
    """
    return subprocess.run(
        [str(openclaw_binary()), 'config', 'validate'],
        env=scratch_environment(home, config),
        capture_output=True,
        text=True,
        timeout=180,
        check=False,
    )


@lru_cache(maxsize=1)
def cli_version() -> str:
    """Return what ``openclaw --version`` says, for the failure report.

    ``node/`` is installed unpinned and never refreshed, so the suite can be
    green against a build that is months behind the one the Pi runs.  Nothing
    hermetic can fix that, but a failure that names the build it disagreed with
    is one a future reader can resolve without re-deriving it.
    """
    with tempfile.TemporaryDirectory() as home:
        completed = subprocess.run(
            [str(openclaw_binary()), '--version'],
            env=scratch_environment(Path(home), shipped_fragment()),
            capture_output=True, text=True, timeout=60, check=False)
    return completed.stdout.strip() or f'unknown (exit {completed.returncode})'


def report(completed: subprocess.CompletedProcess) -> str:
    """Return the CLI's own words, so the next drift is diagnosable in one read."""
    return (f'{cli_version()} said: exit {completed.returncode}\n'
            f'--- stdout ---\n{completed.stdout}\n'
            f'--- stderr ---\n{completed.stderr}')


def test_the_cli_is_installed_where_the_suite_expects_it():
    """The guard below is only a guard if the binary is really there.

    Split out from the validate tests so "nobody ran ``install-openclaw``"
    reads as itself rather than as "the config is broken".
    """
    binary = openclaw_binary()
    assert os.access(binary, os.X_OK), f'{binary} is not executable'
    assert shutil.which('node'), 'node is not on PATH; run the suite inside the pixi env'


def test_the_shipped_fragment_is_accepted_by_the_installed_openclaw(tmp_path):
    """The whole point of #52: the file OpenClaw is handed is one it accepts."""
    completed = validate(shipped_fragment(), tmp_path)
    assert completed.returncode == 0, report(completed)


#: Ways of breaking the fragment that the CLI must reject.  The first two are
#: the exact defects #52 was filed about, put back; the third is a key that
#: never existed, because ``additionalProperties: false`` is the reason the
#: validator catches a *typo* and not just a wrong shape.  All three live under
#: ``agents``, so the error message can be checked for where it points.
REJECTED_MUTATIONS = {
    'agents.entries instead of agents.list': lambda fragment: fragment['agents'].update(
        entries={entry['id']: entry for entry in fragment['agents'].pop('list')}),
    'sandbox.mode "read-only" is not in the enum': lambda fragment: fragment[
        'agents']['list'][0]['sandbox'].update(mode='read-only'),
    'an invented key inside the agent entry': lambda fragment: fragment[
        'agents']['list'][0].update(promptFile='AGENTS.md'),
}


@pytest.mark.parametrize('mutation', sorted(REJECTED_MUTATIONS))
def test_the_validator_rejects_a_broken_fragment(mutation, tmp_path):
    """Break the fragment; the CLI must say so, and say where.

    This is the negative control, and it is the whole reason the test above is
    worth anything: a shell-out that only ever asserts ``returncode == 0``
    passes just as happily against a binary that has become a no-op, or against
    a future ``config validate`` that stops validating.  Verified by
    substitution -- with ``openclaw`` replaced by a shell script whose only
    statement is ``exit 0``, the positive test still passes and all three of
    these go red.
    """
    fragment = json.loads(shipped_fragment().read_text(encoding='utf-8'))
    REJECTED_MUTATIONS[mutation](fragment)

    corrupted = tmp_path / 'corrupted.json'
    corrupted.write_text(json.dumps(fragment, indent=2), encoding='utf-8')
    completed = validate(corrupted, tmp_path)

    assert completed.returncode != 0, report(completed)
    assert 'agents' in completed.stdout + completed.stderr, report(completed)


def test_validating_writes_only_where_the_test_told_it_to(tmp_path):
    """The subprocess is hermetic, so running the suite is not a side effect.

    ``openclaw`` opens a sqlite state DB on startup: at
    ``$OPENCLAW_STATE_DIR/state/openclaw.sqlite`` when that is set, and at
    ``$HOME/.openclaw/state/openclaw.sqlite`` when it is not (both observed).
    If the redirection in ``validate()`` ever stopped working, this suite would
    silently start mutating the developer's real OpenClaw install -- and the
    validate tests would still pass, so nothing else here would notice.
    """
    home = tmp_path / 'home'
    home.mkdir()
    before = shipped_fragment().read_bytes()
    completed = validate(shipped_fragment(), home)

    assert completed.returncode == 0, report(completed)
    assert (home / 'state').is_dir(), (
        f'state did not follow OPENCLAW_STATE_DIR: {sorted(home.iterdir())}')
    assert not (home / '.openclaw').exists(), (
        'the CLI fell back to the default state location; a real $HOME would '
        'have been written to')
    # `config validate` reads; `openclaw doctor --fix` and `config set` write.
    # Pointing OPENCLAW_CONFIG_PATH at the tracked source file is only safe
    # while that stays true of the subcommand this suite runs.
    assert shipped_fragment().read_bytes() == before, (
        'the CLI rewrote the fragment in the source tree')


def test_the_child_inherits_no_openclaw_variable_we_did_not_set(tmp_path, monkeypatch):
    """The hermeticity claim is *total*, not a list of variables we thought of.

    ``OPENCLAW_HOME`` outranks ``HOME`` -- with both set and
    ``OPENCLAW_STATE_DIR`` unset, ``config validate`` writes
    ``$OPENCLAW_HOME/.openclaw/state/openclaw.sqlite`` and leaves ``$HOME``
    empty (measured).  ``OPENCLAW_PROFILE`` namespaces state paths, and the
    installed CLI reads 485 distinct ``OPENCLAW_*`` names.  Enumerating the
    dangerous ones is a losing game, so the whole prefix is dropped.

    Asserted on the environment rather than on the filesystem, deliberately.
    An end-to-end version would be *vacuous today*: with ``OPENCLAW_STATE_DIR``
    also set, neither ``OPENCLAW_HOME`` nor ``OPENCLAW_PROFILE`` moves anything
    `config validate` writes (measured -- the decoy directory stays empty
    either way), so the subprocess cannot distinguish the two policies. What
    can be stated honestly is what the child is handed, and that is what this
    checks.  ``PATH`` is asserted *present* for the opposite reason: the CLI is
    a ``#!/usr/bin/env node`` shim, so over-stripping would break it.
    """
    monkeypatch.setenv('OPENCLAW_HOME', str(tmp_path / 'decoy'))
    monkeypatch.setenv('OPENCLAW_PROFILE', 'someone-elses-profile')
    monkeypatch.setenv('OPENCLAW_A_VARIABLE_NOBODY_HERE_HAS_HEARD_OF', 'x')

    environment = scratch_environment(tmp_path, shipped_fragment())

    assert {key: value for key, value in environment.items()
            if key.startswith('OPENCLAW_')} == {
        'OPENCLAW_STATE_DIR': str(tmp_path / 'state'),
        'OPENCLAW_CONFIG_PATH': str(shipped_fragment()),
    }
    assert environment['HOME'] == str(tmp_path)
    assert environment.get('PATH') == os.environ.get('PATH'), (
        'PATH must survive: the CLI is a `#!/usr/bin/env node` shim')
