#!/usr/bin/env python3
# Copyright (c) 2026 Jaime C.
#
# Use of this source code is governed by an MIT-style
# license that can be found in the LICENSE file or at
# https://opensource.org/licenses/MIT.

"""Run the workspace test suite and refuse to call a hollow run "green".

``colcon test`` reports success for a package that collected **zero** tests,
and ``colcon test-result`` hides such a result unless ``--all`` is passed
(it only fails on errors/failures, never on emptiness). A package can also
produce no result file at all -- colcon's ``unittest`` fallback testing step
writes no JUnit XML whatsoever -- and that silence is likewise reported as
success. Either way a "green" merge can be hollow.

This module is both:

* a **guard** -- :func:`audit` reads the JUnit XML that ``colcon test``
  already produced and fails when an expected package has no result file or
  a result reporting zero collected tests. It never runs tests itself, so it
  can only ever report what colcon actually did; and
* the **driver** ``pixi run test`` invokes, which deletes stale results, runs
  ``colcon test``, runs the workspace-tooling suite (the tests for this very
  file), surfaces every result via ``colcon test-result --all``, and then
  audits. Every stage runs even if an earlier one failed, so the per-package
  summary is always printed; the exit code is non-zero if any stage failed.

Usage::

    python scripts/check_test_integrity.py                  # full honest run
    python scripts/check_test_integrity.py --audit-only     # just re-read XML
    python scripts/check_test_integrity.py --packages-select robot_skills
"""

import argparse
import os
from pathlib import Path
import subprocess
import sys
import time
from xml.etree import ElementTree

#: Pseudo-package name under which the tests for this script are recorded.
#: The guard holds its own test suite to the same standard as a ROS package.
TOOLING_PACKAGE = '_workspace_tooling'

#: Name colcon's pytest step gives the placeholder test case it writes before
#: invoking pytest, so an early crash still leaves a result file behind. A
#: result containing it is evidence of a *failed* run, not of collected tests.
MISSING_RESULT_TESTCASE = 'pytest.missing_result'

#: Allowance (seconds) for filesystem timestamp granularity when judging
#: whether a result file was written by the run we just performed.
MTIME_TOLERANCE = 2.0

_STATUS_OK = 'ok'
_STATUS_NO_RESULT = 'no-result'
_STATUS_ZERO_TESTS = 'zero-tests'
_STATUS_STALE = 'stale'


class PackageAudit:
    """The verdict on one expected package's test results."""

    def __init__(self, name, *, status, tests=0, errors=0, failures=0,
                 result_files=(), detail=''):
        """Record a verdict; see :func:`audit_package` for how it is derived."""
        self.name = name
        self.status = status
        self.tests = tests
        self.errors = errors
        self.failures = failures
        self.result_files = list(result_files)
        self.detail = detail

    @property
    def ok(self):
        """Return True when this package produced a fresh, non-empty result."""
        return self.status == _STATUS_OK

    def __repr__(self):  # noqa: D105
        return (f'PackageAudit({self.name!r}, status={self.status!r}, '
                f'tests={self.tests})')


def find_source_packages(source_dir):
    """Return the sorted names of every ROS package under ``source_dir``.

    The expected set is read from the **source tree**, not from whatever
    happens to exist under ``build/``: a package that silently stops being
    tested must still be expected, and therefore still be caught.

    ``COLCON_IGNORE`` / ``AMENT_IGNORE`` markers are deliberately *not*
    honoured -- dropping a package out of the test run is exactly the failure
    mode this guard exists to detect, so it must not be possible to opt out
    of the guard by opting out of colcon.
    """
    names = []
    for dirpath, dirnames, filenames in os.walk(str(source_dir)):
        dirnames[:] = sorted(d for d in dirnames if not d.startswith('.'))
        if 'package.xml' not in filenames:
            continue
        # A package.xml marks a package root; do not descend into it.
        dirnames[:] = []
        manifest = Path(dirpath) / 'package.xml'
        name = _package_name(manifest)
        if name is None:
            raise ValueError(f'{manifest}: no <name> element')
        names.append(name)
    return sorted(names)


def _package_name(manifest):
    root = ElementTree.parse(str(manifest)).getroot()
    element = root.find('name')
    if element is None or not (element.text or '').strip():
        return None
    return element.text.strip()


def parse_xunit(path):
    """Return ``(tests, errors, failures, sentinel_only)`` for a JUnit XML file.

    Returns ``None`` when the file is not a JUnit result at all (wrong root
    tag, unparseable, or a suite missing the required ``tests`` attribute).
    This mirrors ``colcon_test_result``'s own xunit parser, which skips such
    files rather than failing, so the guard counts exactly the files colcon
    counts. ``sentinel_only`` is True when every test case in the file is
    colcon's ``pytest.missing_result`` placeholder.
    """
    try:
        root = ElementTree.parse(str(path)).getroot()
    except ElementTree.ParseError:
        return None
    if root.tag == 'testsuites':
        suites = [child for child in root if child.tag == 'testsuite']
    elif root.tag == 'testsuite':
        suites = [root]
    else:
        return None

    tests = errors = failures = 0
    for suite in suites:
        try:
            counts = (int(suite.attrib['tests']),
                      int(suite.attrib.get('errors', 0)),
                      int(suite.attrib['failures']))
        except (KeyError, ValueError):
            return None
        if any(count < 0 for count in counts):
            return None
        tests += counts[0]
        errors += counts[1]
        failures += counts[2]

    cases = list(root.iter('testcase'))
    sentinel_only = bool(cases) and all(
        case.get('name') == MISSING_RESULT_TESTCASE for case in cases)
    return tests, errors, failures, sentinel_only


def find_result_files(directory):
    """Return the sorted JUnit result files under ``directory``.

    Walks recursively (skipping dot-directories) the way colcon does, so
    packages whose test step writes several XML files, or writes them into a
    subdirectory, are handled the same way colcon handles them.
    """
    found = []
    for dirpath, dirnames, filenames in os.walk(str(directory)):
        dirnames[:] = sorted(d for d in dirnames if not d.startswith('.'))
        for filename in sorted(filenames):
            if not filename.endswith('.xml'):
                continue
            path = Path(dirpath) / filename
            if parse_xunit(path) is not None:
                found.append(path)
    return found


def audit_package(name, build_base, *, min_mtime=None):
    """Audit one package's results under ``build_base`` and return a verdict.

    A package fails when it produced no parseable result file, when every
    result file predates ``min_mtime`` (a leftover from an earlier run), or
    when its results report zero collected tests. A result whose only test
    case is colcon's ``pytest.missing_result`` placeholder counts as no
    result at all -- it is the record of a run that never happened.
    """
    directory = Path(build_base) / name
    files = find_result_files(directory) if directory.is_dir() else []
    if not files:
        return PackageAudit(
            name, status=_STATUS_NO_RESULT,
            detail=f'no JUnit result file under {directory}')

    if min_mtime is not None:
        fresh = [f for f in files
                 if f.stat().st_mtime >= min_mtime - MTIME_TOLERANCE]
        if not fresh:
            newest = max(f.stat().st_mtime for f in files)
            return PackageAudit(
                name, status=_STATUS_STALE, result_files=files,
                detail='only stale results ({}, newest {:.0f}s before this '
                       'run)'.format(_join(files), min_mtime - newest))
        files = fresh

    tests = errors = failures = 0
    sentinel_only = True
    for path in files:
        parsed = parse_xunit(path)
        tests += parsed[0]
        errors += parsed[1]
        failures += parsed[2]
        sentinel_only = sentinel_only and parsed[3]

    if sentinel_only:
        return PackageAudit(
            name, status=_STATUS_NO_RESULT, result_files=files,
            detail=f'{_join(files)} holds only the colcon '
                   f'{MISSING_RESULT_TESTCASE} placeholder: the test '
                   f'invocation never produced a result')
    if tests == 0:
        return PackageAudit(
            name, status=_STATUS_ZERO_TESTS, result_files=files,
            errors=errors, failures=failures,
            detail=f'{_join(files)} reports 0 collected tests')
    return PackageAudit(
        name, status=_STATUS_OK, tests=tests, errors=errors,
        failures=failures, result_files=files)


def _join(paths):
    return ', '.join(str(p) for p in paths)


def audit(packages, build_base, *, min_mtime=None):
    """Audit every expected package and return the list of verdicts."""
    return [audit_package(name, build_base, min_mtime=min_mtime)
            for name in packages]


def unexpected_result_dirs(packages, build_base):
    """Return build subdirectories holding results for unexpected packages.

    Reported for information only: they are usually leftovers from a package
    that was renamed or removed, and are never a reason to fail.
    """
    build_base = Path(build_base)
    if not build_base.is_dir():
        return []
    expected = set(packages)
    return sorted(
        d.name for d in build_base.iterdir()
        if d.is_dir() and d.name not in expected and find_result_files(d))


def format_report(audits, *, extras=()):
    """Render the per-package summary printed on both success and failure."""
    width = max([len(a.name) for a in audits] + [len('package')])
    lines = [
        '',
        '=== test integrity audit ' + '=' * (width + 9),
        f'{"package".ljust(width)}  tests  errors  failures  status',
    ]
    for a in sorted(audits, key=lambda a: a.name):
        lines.append(
            f'{a.name.ljust(width)}  {a.tests:5d}  {a.errors:6d}  '
            f'{a.failures:8d}  {a.status}')
    lines.append('-' * (width + 34))
    total = sum(a.tests for a in audits)
    lines.append(
        f'{len(audits)} packages, {total} tests collected')
    for name in extras:
        lines.append(
            f'note: build/{name} holds results for a package that is not in '
            f'the source tree (leftover?)')

    bad = [a for a in audits if not a.ok]
    for a in sorted(bad, key=lambda a: a.name):
        lines.append(f'FAIL {a.name}: {a.detail}')
    if bad:
        lines.append(
            f'AUDIT FAILED: {len(bad)} of {len(audits)} packages did not '
            f'produce a usable test result')
    else:
        lines.append('AUDIT PASSED: every expected package collected tests')
    lines.append('')
    return '\n'.join(lines)


def delete_result_files(build_base, packages=None):
    """Delete the JUnit result files under ``build_base``.

    Removing results *before* the run is the primary defence against a stale
    artifact making a skipped package look tested: after this, any result
    file that exists was written by the run in progress. Only files that
    parse as JUnit are removed, so ``package.xml`` and friends are untouched.
    When ``packages`` is given only those packages' results are removed, so a
    narrowed run does not destroy evidence it is not going to regenerate.
    """
    build_base = Path(build_base)
    if not build_base.is_dir():
        return []
    if packages is None:
        roots = [build_base]
    else:
        roots = [build_base / name for name in packages]
    removed = []
    for root in roots:
        if not root.is_dir():
            continue
        for path in find_result_files(root):
            path.unlink()
            removed.append(path)
    return sorted(removed)


def _run(cmd, *, cwd=None):
    print('+ ' + ' '.join(str(c) for c in cmd), flush=True)
    return subprocess.run(cmd, cwd=cwd).returncode


def run_tooling_tests(repo_root, build_base):
    """Run the tests for this script, recording them like a colcon package.

    They are written to ``build/<TOOLING_PACKAGE>/pytest.xml`` so that the
    audit -- and ``colcon test-result`` -- treat the guard's own suite
    exactly like a ROS package's: deleting or emptying it is caught by the
    guard itself.
    """
    build_dir = Path(build_base) / TOOLING_PACKAGE
    build_dir.mkdir(parents=True, exist_ok=True)
    cmd = [
        sys.executable, '-m', 'pytest', 'scripts/tests',
        '--tb=short',
        f'--junit-xml={build_dir / "pytest.xml"}',
        f'--junit-prefix={TOOLING_PACKAGE}',
        '-o', f'cache_dir={build_dir / ".pytest_cache"}',
        # Same RoboStack plugin incompatibility the per-package pytest.ini
        # files work around; see src/robot_backends/pytest.ini.
        '-p', 'no:launch_testing',
        '-p', 'no:launch_ros',
    ]
    return _run(cmd, cwd=str(repo_root))


def _extras(narrowed, packages, args):
    # A narrowed run deliberately ignores most of the workspace, so every
    # other build directory would be reported as "unexpected" -- pure noise.
    if narrowed:
        return []
    return unexpected_result_dirs(packages, args.build_base)


def main(argv=None):
    """Parse arguments, run the requested stages, and return an exit code."""
    repo_root = Path(__file__).resolve().parent.parent
    parser = argparse.ArgumentParser(
        description=__doc__.splitlines()[0],
        formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument(
        '--source-dir', default=str(repo_root / 'src'),
        help='workspace source directory (default: %(default)s)')
    parser.add_argument(
        '--build-base', default=str(repo_root / 'build'),
        help='colcon build base holding the results (default: %(default)s)')
    parser.add_argument(
        '--audit-only', action='store_true',
        help='do not run anything; audit the results already in the build '
             'base (no freshness check is possible in this mode)')
    parser.add_argument(
        '--packages-select', nargs='+', metavar='NAME', default=None,
        help='narrow the run to these packages; the result is a PARTIAL '
             'run and is not a whole-workspace verdict')
    args = parser.parse_args(argv)

    packages = find_source_packages(args.source_dir)
    narrowed = args.packages_select is not None
    if narrowed:
        unknown = sorted(set(args.packages_select) - set(packages))
        if unknown:
            parser.error(
                'no such package in {}: {}'.format(
                    args.source_dir, ', '.join(unknown)))
        packages = sorted(args.packages_select)
    else:
        packages.append(TOOLING_PACKAGE)

    if args.audit_only:
        audits = audit(packages, args.build_base)
        print(format_report(audits, extras=_extras(narrowed, packages, args)))
        return 0 if all(a.ok for a in audits) else 1

    if narrowed:
        print('*** PARTIAL RUN: only {} -- not a whole-workspace verdict ***'
              .format(', '.join(packages)), flush=True)

    removed = delete_result_files(
        args.build_base, packages if narrowed else None)
    print(f'+ removed {len(removed)} stale test result file(s)', flush=True)

    started = time.time()
    colcon_test = ['colcon', 'test']
    if narrowed:
        colcon_test += ['--packages-select'] + packages
    rc_test = _run(colcon_test, cwd=str(repo_root))

    rc_tooling = 0
    if not narrowed:
        rc_tooling = run_tooling_tests(repo_root, args.build_base)

    # --all so zero-error results (including the empty ones this guard is
    # about) are visible in the log rather than silently omitted.
    rc_result = _run(['colcon', 'test-result', '--all', '--verbose'],
                     cwd=str(repo_root))

    audits = audit(packages, args.build_base, min_mtime=started)
    print(format_report(audits, extras=_extras(narrowed, packages, args)))
    rc_audit = 0 if all(a.ok for a in audits) else 1

    stages = {
        'colcon test': rc_test,
        'workspace-tooling tests': rc_tooling,
        'colcon test-result': rc_result,
        'test integrity audit': rc_audit,
    }
    failed = [name for name, rc in stages.items() if rc]
    if failed:
        print('FAILED stages: ' + ', '.join(failed))
        return 1
    print('All stages passed.')
    return 0


if __name__ == '__main__':
    sys.exit(main())
