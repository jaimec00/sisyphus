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
  already produced and fails when an expected package has no result file, a
  result reporting zero collected tests, or a result in which every collected
  test was skipped. It never runs tests itself, so it can only ever report
  what colcon actually did; and
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
import collections
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

#: Attributes ``colcon_test_result`` sums into its skipped count
#: (``colcon_test_result/test_result/xunit.py:108-115``); mirrored here so the
#: guard discounts exactly the tests colcon considers skipped.
SKIPPED_ATTRIBUTES = ('skip', 'skipped', 'disabled')

#: Counts read from one JUnit XML file. ``sentinel_only`` is True when every
#: test case in the file is colcon's placeholder (see above).
XUnitCounts = collections.namedtuple(
    'XUnitCounts', 'tests errors failures skipped sentinel_only')

_STATUS_OK = 'ok'
_STATUS_NO_RESULT = 'no-result'
_STATUS_ZERO_TESTS = 'zero-tests'
_STATUS_ALL_SKIPPED = 'all-skipped'
_STATUS_STALE = 'stale'


class PackageAudit:
    """The verdict on one expected package's test results."""

    def __init__(self, name, *, status, tests=0, errors=0, failures=0,
                 skipped=0, result_files=(), newest_mtime=None, detail=''):
        """Record a verdict; see :func:`audit_package` for how it is derived."""
        self.name = name
        self.status = status
        self.tests = tests
        self.errors = errors
        self.failures = failures
        self.skipped = skipped
        self.result_files = list(result_files)
        self.newest_mtime = newest_mtime
        self.detail = detail

    @property
    def executed(self):
        """Return the number of test bodies that actually ran."""
        return self.tests - self.skipped

    @property
    def ok(self):
        """Return True when this package produced a fresh, non-empty result."""
        return self.status == _STATUS_OK

    def __repr__(self):  # noqa: D105
        return (f'PackageAudit({self.name!r}, status={self.status!r}, '
                f'tests={self.tests})')


def find_manifests(source_dir):
    """Return ``(name, package.xml path)`` for every package under a tree.

    The expected set is read from the **source tree**, not from whatever
    happens to exist under ``build/``: a package that silently stops being
    tested must still be expected, and therefore still be caught.

    ``COLCON_IGNORE`` / ``AMENT_IGNORE`` markers are deliberately *not*
    honoured -- dropping a package out of the test run is exactly the failure
    mode this guard exists to detect, so it must not be possible to opt out
    of the guard by opting out of colcon.
    """
    found = []
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
        found.append((name, manifest))
    return sorted(found)


def _git(source_dir, *arguments):
    """Run git in ``source_dir``; return its stdout, or None if it failed."""
    try:
        completed = subprocess.run(
            ['git', '-C', str(source_dir), *arguments],
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, check=False)
    except OSError:
        return None
    return completed.stdout if completed.returncode == 0 else None


def _git_tracked_manifests(source_dir):
    """Return the resolved ``package.xml`` paths git tracks under a tree.

    Returns ``None`` when there is no ownership signal to be had -- git is not
    installed, or ``source_dir`` is not inside a work tree (a tarball export,
    say). The caller then expects every package it found, erring towards a
    loud failure rather than a silent exemption. An *empty* set, by contrast,
    is a real answer: git is there and tracks none of these manifests.
    """
    inside = _git(source_dir, 'rev-parse', '--is-inside-work-tree')
    if inside is None or inside.strip() != b'true':
        return None
    listing = _git(source_dir, 'ls-files', '-z')
    if listing is None:
        return None
    source_dir = Path(source_dir)
    return {
        (source_dir / rel).resolve()
        for rel in listing.decode('utf-8', 'replace').split('\0')
        if rel and Path(rel).name == 'package.xml'}


def discover_packages(source_dir):
    """Return ``(expected, unowned)`` package names found under ``source_dir``.

    ``expected`` holds the packages **this repository owns** -- the ones whose
    ``package.xml`` git tracks. ``unowned`` holds packages that exist in the
    source tree without being tracked: ``vcs import`` / ``robot.repos`` drops
    third-party sources there, and this repo cannot add tests to vendored
    upstream code, so demanding results from them would make ``pixi run test``
    permanently and unfixably red -- which is a fast route to people bypassing
    the driver altogether.

    Ownership is decided by git rather than by a marker file inside the
    package, so a first-party package still cannot opt out of the guard: the
    only escape is removing its manifest from the index, which is a visible,
    reviewable change. ``COLCON_IGNORE`` and ``.gitignore`` remain powerless
    (an ignored-but-tracked file is still tracked).
    """
    manifests = find_manifests(source_dir)
    tracked = _git_tracked_manifests(source_dir)
    if tracked is None:
        return sorted(name for name, _ in manifests), []
    expected, unowned = [], []
    for name, manifest in manifests:
        target = expected if manifest.resolve() in tracked else unowned
        target.append(name)
    return sorted(expected), sorted(unowned)


def find_source_packages(source_dir):
    """Return the sorted names of the packages this repo owns and must test."""
    return discover_packages(source_dir)[0]


def _package_name(manifest):
    root = ElementTree.parse(str(manifest)).getroot()
    element = root.find('name')
    if element is None or not (element.text or '').strip():
        return None
    return element.text.strip()


def parse_xunit(path):
    """Return an :data:`XUnitCounts` for a JUnit XML file, or ``None``.

    ``None`` means the file is not a JUnit result at all: unparseable, a root
    tag that is neither ``testsuite`` nor ``testsuites``, a suite missing a
    required attribute (``tests`` or ``failures``), or a count that is not a
    non-negative integer. Those are exactly the rules
    ``colcon_test_result``'s own xunit parser applies -- it requires ``tests``
    and ``failures``, defaults ``errors`` and the three skip attributes to 0,
    and skips any file that violates them
    (``colcon_test_result/test_result/xunit.py:99-133``) -- so the guard
    counts exactly the files colcon counts.
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

    tests = errors = failures = skipped = 0
    for suite in suites:
        try:
            counts = [int(suite.attrib['tests']),
                      int(suite.attrib.get('errors', 0)),
                      int(suite.attrib['failures'])]
            counts.append(sum(int(suite.attrib.get(attribute, 0))
                              for attribute in SKIPPED_ATTRIBUTES))
        except (KeyError, ValueError):
            return None
        if any(count < 0 for count in counts):
            return None
        tests += counts[0]
        errors += counts[1]
        failures += counts[2]
        skipped += counts[3]

    cases = list(root.iter('testcase'))
    sentinel_only = bool(cases) and all(
        case.get('name') == MISSING_RESULT_TESTCASE for case in cases)
    return XUnitCounts(tests, errors, failures, skipped, sentinel_only)


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
    result file predates ``min_mtime`` (a leftover from an earlier run), when
    its results report zero collected tests, or when every collected test was
    skipped (``pytest.importorskip`` on a missing dependency, a blanket
    ``@pytest.mark.skip``, a hardware-gated suite): a suite in which no test
    body executed is the same hollow green as an empty one, and colcon calls
    both of them success. A result whose only test case is colcon's
    ``pytest.missing_result`` placeholder counts as no result at all -- it is
    the record of a run that never happened.
    """
    directory = Path(build_base) / name
    files = find_result_files(directory) if directory.is_dir() else []
    if not files:
        return PackageAudit(
            name, status=_STATUS_NO_RESULT,
            detail=f'no JUnit result file under {directory}')

    newest = max(f.stat().st_mtime for f in files)
    if min_mtime is not None:
        fresh = [f for f in files
                 if f.stat().st_mtime >= min_mtime - MTIME_TOLERANCE]
        if not fresh:
            return PackageAudit(
                name, status=_STATUS_STALE, result_files=files,
                newest_mtime=newest,
                detail='only stale results ({}, newest {:.0f}s before this '
                       'run)'.format(_join(files), min_mtime - newest))
        files = fresh
        newest = max(f.stat().st_mtime for f in files)

    tests = errors = failures = skipped = 0
    sentinel_only = True
    for path in files:
        parsed = parse_xunit(path)
        tests += parsed.tests
        errors += parsed.errors
        failures += parsed.failures
        skipped += parsed.skipped
        sentinel_only = sentinel_only and parsed.sentinel_only

    verdict = PackageAudit(
        name, status=_STATUS_OK, tests=tests, errors=errors,
        failures=failures, skipped=skipped, result_files=files,
        newest_mtime=newest)
    if sentinel_only:
        verdict.status = _STATUS_NO_RESULT
        verdict.tests = verdict.skipped = 0
        verdict.detail = (
            f'{_join(files)} holds only the colcon '
            f'{MISSING_RESULT_TESTCASE} placeholder: the test invocation '
            f'never produced a result')
    elif tests == 0:
        verdict.status = _STATUS_ZERO_TESTS
        verdict.detail = f'{_join(files)} reports 0 collected tests'
    elif verdict.executed <= 0:
        verdict.status = _STATUS_ALL_SKIPPED
        verdict.detail = (
            f'{_join(files)} reports {tests} collected tests but all '
            f'{skipped} were skipped: no test body ran')
    return verdict


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


def format_age(seconds):
    """Render an age in seconds as a short human-readable string."""
    seconds = max(0.0, float(seconds))
    for limit, unit, divisor in (
        (90, 's', 1), (90 * 60, 'm', 60), (48 * 3600, 'h', 3600),
    ):
        if seconds < limit:
            return f'{seconds / divisor:.0f}{unit}'
    return f'{seconds / 86400:.0f}d'


def format_report(audits, *, notes=(), show_age=False):
    """Render the per-package summary printed on both success and failure."""
    width = max([len(a.name) for a in audits] + [len('package')])
    header = (f'{"package".ljust(width)}  tests  skipped  errors  failures'
              f'  status')
    if show_age:
        header += '     age'
    lines = [
        '',
        '=== test integrity audit ' + '=' * max(0, len(header) - 25),
        header,
    ]
    now = time.time()
    for a in sorted(audits, key=lambda a: a.name):
        row = (f'{a.name.ljust(width)}  {a.tests:5d}  {a.skipped:7d}  '
               f'{a.errors:6d}  {a.failures:8d}  {a.status.ljust(6)}')
        if show_age:
            age = ('-' if a.newest_mtime is None
                   else format_age(now - a.newest_mtime))
            row += f'  {age:>6}'
        lines.append(row.rstrip())
    lines.append('-' * len(header))
    total = sum(a.tests for a in audits)
    total_skipped = sum(a.skipped for a in audits)
    summary = f'{len(audits)} packages, {total} tests collected'
    if total_skipped:
        summary += f' ({total_skipped} skipped)'
    lines.append(summary)
    lines.extend(notes)

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
    try:
        return subprocess.run(cmd, cwd=cwd).returncode
    except FileNotFoundError:
        # A traceback here reads like a bug in the guard; it is almost always
        # a shell that is not inside the pixi environment.
        print(f'error: {cmd[0]}: command not found -- run this through '
              f'`pixi run test` or from inside `pixi shell`', flush=True)
        return 127


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


def _notes(narrowed, packages, unowned, args):
    """Render the informational lines appended to the report."""
    notes = [
        f'note: {name} is in the source tree but not tracked by this '
        f'repository (vendored/imported?), so it is not audited'
        for name in unowned]
    # A narrowed run deliberately ignores most of the workspace, so every
    # other build directory would be reported as "unexpected" -- pure noise.
    if narrowed:
        return notes
    return notes + [
        f'note: build/{name} holds results for a package that is not in '
        f'the source tree (leftover?)'
        for name in unexpected_result_dirs(packages, args.build_base)]


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
             'base. There is no freshness check in this mode -- the report '
             'gains an age column so stale evidence is self-evident')
    parser.add_argument(
        '--packages-select', nargs='+', metavar='NAME', default=None,
        help='narrow the run to these packages; the result is a PARTIAL '
             'run and is not a whole-workspace verdict')
    args = parser.parse_args(argv)

    if not Path(args.source_dir).is_dir():
        parser.error(f'--source-dir is not a directory: {args.source_dir}')
    packages, unowned = discover_packages(args.source_dir)
    if not packages:
        # Refusing to pass here is the whole point: an audit that found
        # nothing to audit must not print "AUDIT PASSED".
        parser.error(
            f'found no packages owned by this repository under '
            f'{args.source_dir} -- wrong --source-dir, or the manifests are '
            f'untracked; refusing to report a passing audit of nothing')

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

    notes = _notes(narrowed, packages, unowned, args)

    if args.audit_only:
        audits = audit(packages, args.build_base)
        print('*** --audit-only: nothing was re-run; the ages below are how '
              'old this evidence is ***', flush=True)
        print(format_report(audits, notes=notes, show_age=True))
        return 0 if all(a.ok for a in audits) else 1

    if narrowed:
        print('*** PARTIAL RUN: only {} -- not a whole-workspace verdict ***'
              .format(', '.join(packages)), flush=True)

    removed = delete_result_files(
        args.build_base, packages if narrowed else None)
    print(f'+ removed {len(removed)} stale test result file(s)', flush=True)

    started = time.time()
    # --base-paths/--build-base/--test-result-base keep colcon and the audit
    # pointed at the same directories when the defaults are overridden.
    colcon_test = ['colcon', 'test',
                   '--base-paths', str(args.source_dir),
                   '--build-base', str(args.build_base),
                   '--test-result-base', str(args.build_base)]
    if narrowed:
        colcon_test += ['--packages-select'] + packages
    rc_test = _run(colcon_test, cwd=str(repo_root))

    rc_tooling = 0
    if not narrowed:
        rc_tooling = run_tooling_tests(repo_root, args.build_base)

    # --all so zero-error results (including the empty ones this guard is
    # about) are visible in the log rather than silently omitted.
    rc_result = _run(['colcon', 'test-result', '--all', '--verbose',
                      '--test-result-base', str(args.build_base)],
                     cwd=str(repo_root))

    audits = audit(packages, args.build_base, min_mtime=started)
    print(format_report(audits, notes=notes))
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
