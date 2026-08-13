# Copyright (c) 2026 Jaime C.
#
# Use of this source code is governed by an MIT-style
# license that can be found in the LICENSE file or at
# https://opensource.org/licenses/MIT.

"""Boot the MCP server the way the deployment boots it: through the launcher.

``src/robot_mcp/test/test_stdio_transport.py`` already proves ``python -m
robot_mcp`` is a working MCP server -- but it proves it from a test process
that hands the child its own ``sys.path``, so it says nothing about how the
child would ever get that path in production. That gap is the #55 bug: the
deploy path carried a hand-written ``PYTHONPATH`` list, ``robot_world`` was
never added to it, the server died with ``ModuleNotFoundError`` on the Pi, and
the whole gate stayed green because nothing in it ever ran the launch string.

So this file runs ``scripts/robot-mcp-launch.sh`` itself, in a child that is
given **no** ``PYTHONPATH`` at all, and asserts a real MCP ``initialize``
comes back. "The gate is green" then means "the server boots the way it is
deployed". And because a smoke test that cannot fail is worse than none, the
same launcher is run against a source tree with one required package removed,
which must fail -- see :func:`test_dropping_a_required_package_breaks_the_boot`.
"""

import os
from pathlib import Path
import subprocess

import anyio
from mcp import ClientSession, stdio_client, StdioServerParameters
import pytest

#: This file is ``<repo>/scripts/tests/test_boot_smoke.py``.
REPO_ROOT = Path(__file__).resolve().parent.parent.parent

#: The launcher under test, at the path a repository root must hold it at.
LAUNCHER = Path('scripts') / 'robot-mcp-launch.sh'

#: The package to remove when proving the smoke test bites. It is the #55
#: package for a reason: ``robot_mcp/server.py`` imports ``robot_world``
#: unconditionally at module load, so its absence is a boot failure rather than
#: a degraded feature -- exactly the failure this whole issue exists to catch.
REQUIRED_PACKAGE = 'robot_world'

#: Everything the child must not inherit from whoever runs the suite.
#: ``PYTHONPATH`` is the load-bearing entry (see
#: :func:`undiscovered_environment`); the other three mirror
#: ``src/robot_mcp/test/mcp_fixtures.py``'s ``INHERITED_ENV_TO_DROP`` -- a
#: stray ``ROBOT_WORLD_STATE`` would point a test server at the developer's
#: real world file. They are spelled out rather than imported because this
#: suite runs outside the workspace packages, which is the point of it.
DROPPED_ENV = ('PYTHONPATH', 'ROS_DOMAIN_ID',
               'ROBOT_WORLD_STATE', 'ROBOT_WORLD_SEED')

#: Whole-handshake budget, wide enough for a cold interpreter on slow hardware.
#: A server that comes up but never answers must fail the run, not stall it;
#: nothing else here would ever time out on its own.
BOOT_TIMEOUT_SECONDS = 30.0

#: A manifest is the only thing discovery looks for, so a fake one need only
#: be a manifest by name -- but keep it parseable, so a future check that
#: reads it (colcon's, the audit's) is not surprised by these fixtures.
MANIFEST = ('<?xml version="1.0"?>\n'
            '<package format="3"><name>{name}</name></package>\n')

#: A stand-in for ``python`` that starts no server and reports what the
#: launcher handed it: the discovered path, then one argument per line, so an
#: argument containing a space is distinguishable from two arguments.
INTERPRETER_STUB = ('#!/usr/bin/env bash\necho "$PYTHONPATH"\n'
                    'printf \'%s\\n\' "$@"\n')


def undiscovered_environment() -> dict[str, str]:
    """Return a child environment holding no workspace packages of its own.

    **The single most important function in this file.** If the child inherits
    a ``PYTHONPATH`` that already resolves the workspace -- which is exactly
    what ``mcp_fixtures.clean_environment()`` builds, from the *test runner's*
    ``sys.path`` -- then every test here passes with the launcher's discovery
    completely broken, and the whole feature is theatre. So ``PYTHONPATH`` is
    dropped, and the only way the child can import ``robot_mcp`` is the path
    the launcher computed.

    :func:`test_the_stripped_environment_really_hands_the_child_nothing` holds
    that claim up: it asserts the interpreter cannot find the packages in this
    environment without a launcher.
    """
    return {name: value for name, value in os.environ.items()
            if name not in DROPPED_ENV}


def fake_repository_root(tmp_path: Path, packages) -> Path:
    """Return a repo root borrowing the real launcher and the named packages.

    Both the launcher and each ``src/<pkg>`` are **symlinks** into the real
    tree, so a normal run can never mutate it. The launcher being a symlink is
    deliberate rather than incidental: it is what pins the launcher's lexical
    repo-root resolution. Were ``robot-mcp-launch.sh`` to start doing
    ``readlink -f "${BASH_SOURCE[0]}"``, it would resolve back through this
    symlink to the real ``scripts/``, discover the real ``src/`` -- and the
    bites test below would find every package it was supposed to be missing
    and pass while broken.
    """
    root = tmp_path / 'repo'
    (root / 'scripts').mkdir(parents=True)
    (root / 'src').mkdir()
    (root / LAUNCHER).symlink_to(REPO_ROOT / LAUNCHER)
    for package in packages:
        (root / 'src' / package).symlink_to(REPO_ROOT / 'src' / package)
    return root


def source_packages() -> list[str]:
    """Return every package in the real source tree, by manifest."""
    packages = sorted(manifest.parent.name
                      for manifest in (REPO_ROOT / 'src').glob('*/package.xml'))
    assert REQUIRED_PACKAGE in packages, packages
    return packages


def written_repository_root(tmp_path: Path, packages) -> Path:
    """Return a repo root of *invented* packages, for the discovery tests.

    No symlinks into the real tree here: these tests are about which
    directories the launcher finds, and inventing them keeps the expected
    answer stated in the test rather than read from the workspace.
    """
    root = tmp_path / 'repo'
    (root / 'scripts').mkdir(parents=True)
    (root / 'src').mkdir()
    (root / LAUNCHER).symlink_to(REPO_ROOT / LAUNCHER)
    for package in packages:
        (root / 'src' / package).mkdir()
        (root / 'src' / package / 'package.xml').write_text(
            MANIFEST.format(name=package))
    return root


def run_launcher(root: Path, *arguments, environment=None) -> subprocess.CompletedProcess:
    """Run ``root``'s launcher with ``python`` replaced by a recorder.

    The stub interpreter is what makes discovery observable without booting
    anything: the launcher ``exec``s whatever ``python`` is first on ``PATH``,
    so shadowing it turns "what would the server have been started with" into
    two lines on stdout.
    """
    stub_dir = root.parent / 'stub-bin'
    stub_dir.mkdir(exist_ok=True)
    stub = stub_dir / 'python'
    stub.write_text(INTERPRETER_STUB)
    stub.chmod(0o755)

    environment = dict(undiscovered_environment() if environment is None
                       else environment)
    environment['PATH'] = os.pathsep.join(
        [str(stub_dir), environment.get('PATH', '')])
    return subprocess.run(
        [str(root / LAUNCHER), *arguments], env=environment,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=60)


def handshake(root: Path, errlog) -> str:
    """Return the server name reported by the server ``root``'s launcher starts.

    A plain synchronous test calling :func:`anyio.run`, rather than
    ``pytest.mark.anyio``: ``scripts/tests`` configures no ``anyio_backend``
    fixture, and one handshake does not justify a plugin dependency here.
    """
    parameters = StdioServerParameters(
        command=str(root / LAUNCHER), env=undiscovered_environment())

    async def initialize() -> str:
        with anyio.fail_after(BOOT_TIMEOUT_SECONDS):
            async with stdio_client(parameters, errlog=errlog) as (read, write):
                async with ClientSession(read, write) as session:
                    initialized = await session.initialize()
                    return initialized.server_info.name

    return anyio.run(initialize)


@pytest.fixture
def errlog(tmp_path):
    """Yield an open file capturing the spawned server's stderr.

    Captured rather than passed through, because on the failing path the
    child's traceback *is* the assertion: it distinguishes "the import the
    launcher was supposed to enable failed" from "something else went wrong".
    """
    path = tmp_path / 'server-stderr.txt'
    with path.open('w+') as handle:
        yield handle


def test_every_package_with_a_manifest_lands_on_the_path(tmp_path):
    """Discovery is the manifest and nothing else -- no list, no filter.

    ``papers/`` has no ``package.xml`` and must not be discovered; the two
    that do must both be there, whether or not the server imports them. A
    package added tomorrow is on the path for the same reason.
    """
    root = written_repository_root(tmp_path, ['robot_alpha', 'robot_beta'])
    (root / 'src' / 'papers').mkdir()

    completed = run_launcher(root)
    discovered = completed.stdout.splitlines()[0].split(os.pathsep)

    assert completed.returncode == 0, completed.stderr
    assert set(discovered) == {str(root / 'src' / 'robot_alpha'),
                               str(root / 'src' / 'robot_beta')}


def test_an_inherited_pythonpath_is_appended_not_clobbered(tmp_path):
    """A caller may add to the path; it may not shadow a workspace package.

    So the caller's entries survive (nothing the operator set is thrown away)
    and they come *after* the discovered ones (a stale copy of a workspace
    package elsewhere on the path cannot win).
    """
    root = written_repository_root(tmp_path, ['robot_alpha'])
    environment = undiscovered_environment() | {'PYTHONPATH': '/opt/vendor'}

    completed = run_launcher(root, environment=environment)
    discovered = completed.stdout.splitlines()[0].split(os.pathsep)

    assert completed.returncode == 0, completed.stderr
    assert discovered == [str(root / 'src' / 'robot_alpha'), '/opt/vendor']


def test_the_servers_own_arguments_are_forwarded(tmp_path):
    """``--world-state`` and friends belong to the server, not the launcher.

    One argument per line, and a path with a space in it, so that ``"$@"``
    losing its quotes -- which would split that path into two arguments the
    server cannot use -- is visible here rather than at a deployment.
    """
    root = written_repository_root(tmp_path, ['robot_alpha'])

    completed = run_launcher(root, '--world-state', '/tmp/a world.json')

    assert completed.returncode == 0, completed.stderr
    assert completed.stdout.splitlines()[1:] == [
        '-m', 'robot_mcp', '--world-state', '/tmp/a world.json']


def test_a_root_with_an_empty_source_tree_refuses_to_launch(tmp_path):
    """Discovering nothing is a bug, not an empty path to launch with.

    ``src/`` exists and holds no manifest -- the state a mis-set repo root or a
    half-checked-out tree produces, and the one the glob's unmatched-pattern
    behaviour has to be right about.

    A launcher that silently ``exec``s with an empty ``PYTHONPATH`` would
    reproduce #55 exactly -- a green-looking start followed by an import error
    from somewhere deep -- so it dies here instead, naming itself and the tree
    it looked in.
    """
    root = written_repository_root(tmp_path, [])

    completed = run_launcher(root)

    assert completed.returncode != 0
    assert 'robot-mcp-launch.sh:' in completed.stderr
    assert str(root / 'src') in completed.stderr
    assert not completed.stdout, 'it must not reach the interpreter at all'


def test_the_stripped_environment_really_hands_the_child_nothing():
    """The control for every test below: without the launcher, nothing imports.

    If this ever passes an import, the environment these tests spawn children
    in is *not* stripped, and their green means nothing -- discovery could be
    deleted outright and the child would still find the packages.

    It is spawned the way the launcher's child is spawned, and no other way:
    ``python`` resolved from ``PATH`` (not ``sys.executable``) and the working
    directory inherited (not pinned). The control must not be able to drift
    away from the thing it controls -- if ``cwd`` or the interpreter ever
    *does* start mattering, this must feel it first.
    """
    completed = subprocess.run(
        ['python', '-c', 'import robot_mcp'], env=undiscovered_environment(),
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=60)

    assert completed.returncode != 0
    assert 'No module named' in completed.stderr


def test_the_launcher_boots_a_server_that_answers_initialize(tmp_path, errlog):
    """The gate step: the deployed command, booted, handshaken, over stdio.

    Every workspace package is present, nothing is on ``PYTHONPATH``, and the
    only reason ``robot_mcp`` imports at all is the path the launcher built.
    """
    root = fake_repository_root(tmp_path, source_packages())

    assert handshake(root, errlog) == 'robot_mcp'


def test_dropping_a_required_package_breaks_the_boot(tmp_path, errlog):
    """Prove it bites: remove ``robot_world`` and the same step must fail.

    This is #55 replayed. ``robot_mcp/server.py`` imports ``robot_world`` at
    module load, so a source tree missing it cannot serve -- and the client
    must see that rather than a working server, or the test above is measuring
    something other than discovery.

    The child's stderr is asserted, not just the exception: a hung server
    would also raise (on the timeout), and "it timed out" is not evidence that
    the missing package is what broke it.
    """
    packages = [name for name in source_packages() if name != REQUIRED_PACKAGE]
    root = fake_repository_root(tmp_path, packages)

    with pytest.raises(Exception):
        handshake(root, errlog)

    errlog.seek(0)
    stderr = errlog.read()
    assert f"No module named '{REQUIRED_PACKAGE}'" in stderr, stderr
