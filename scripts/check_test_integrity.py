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

A bar of "more than zero tests" is also too low on its own: a package can
drop from 59 tests to 3 (a stray ``testpaths`` edit, an ``--ignore`` in
``addopts``, a deleted module) and still clear it. So the guard additionally
**ratchets**: ``scripts/test_baseline.json`` records, per package, how many
non-skipped non-linter tests ran, and a package that produces fewer than its
baseline fails. Skipping a test therefore trips the ratchet exactly as
deleting it does -- ``@pytest.mark.skip`` on ten tests removes ten tests'
worth of evidence whether or not they are still collected. A package that
grows implementation code must have non-linter tests at all -- three ament
linter tests are an honest suite for an empty skeleton package, and a
dishonest one the moment the package holds real code.

The floor **maintains itself in one direction**. A full run rewrites the
baseline UP to whatever a package now produces (and creates the entry for a
package that has none), so adding tests carries its own floor bump and no one
has to remember ``--update-baseline``; a run that stays level rewrites
nothing. Going DOWN is the gate, and stays manual: a package below its floor
fails unless the run was explicitly told to allow it (``--allow-decrease``, or
``ALLOW_TEST_DECREASE=1`` in the environment), which re-cuts that floor down
and passes. Neither direction is written from a run that is not otherwise
green -- a floor cut from a broken run would pin the brokenness in place.

Before any of that, the run refuses to proceed on a workspace whose
``package.xml`` files do not parse. colcon does not fail on a malformed
manifest: it logs the parse error at DEBUG, silently reclassifies the package
from ``ament_python`` to plain ``python``, and still reports the build as
successful -- and the package then produces no test result at all, so the
first visible symptom is an audit failure naming neither the file nor the
cause. :func:`validate_manifests` runs ahead of the audit and the ratchet and
says both.

This module is both:

* a **guard** -- :func:`audit` reads the JUnit XML that ``colcon test``
  already produced and fails when an expected package has no result file, a
  result reporting zero collected tests, a result in which every collected
  test was skipped, fewer non-skipped non-linter tests than the baseline
  records, or no non-linter tests at all for a package holding implementation
  code. It never runs tests itself, so it can only ever report what colcon
  actually did; and
* the **driver** ``pixi run test`` invokes, which deletes stale results, runs
  ``colcon test``, runs the workspace-tooling suite (the tests for this very
  file), surfaces every result via ``colcon test-result --all``, audits, and
  then ratchets the baseline. Every stage runs even if an earlier one failed,
  so the per-package summary is always printed; the exit code is non-zero if
  any stage failed.

``--audit-only`` stays **read-only**: it re-reads XML that some earlier run
produced, which is evidence about the past, not about the tree as it now
stands, so it never auto-bumps a floor. Only the full driver -- the run that
produced the results it is judging -- moves the baseline on its own.

Usage::

    python scripts/check_test_integrity.py                  # full honest run
    python scripts/check_test_integrity.py --audit-only     # just re-read XML
    python scripts/check_test_integrity.py --packages-select robot_skills
    python scripts/check_test_integrity.py --allow-decrease   # re-cut down
    python scripts/check_test_integrity.py --update-baseline  # + prune
"""

import argparse
import collections
import json
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

#: Child elements a JUnit ``<testcase>`` carries when its body never ran.
#: pytest writes ``<skipped type="pytest.skip">`` for ``@pytest.mark.skip``,
#: ``pytest.importorskip`` and ``pytest.skip()``, and the same tag with
#: ``type="pytest.xfail"`` for an expected failure -- neither executed, so
#: neither is evidence. The other two spellings mirror
#: :data:`SKIPPED_ATTRIBUTES`, for result writers that use them.
SKIPPED_CASE_TAGS = frozenset({'skipped', 'skip', 'disabled'})

#: Test names (equivalently, test module names) that belong to a linter rather
#: than to the package's own behaviour. Every ament linter test is a single
#: function whose name matches its module's, so matching either is enough --
#: and matching the *name* catches ``scripts/tests/test_lint.py``, which runs
#: the same three linters from a module called something else.
LINTER_TEST_NAMES = frozenset({
    'test_copyright', 'test_flake8', 'test_pep257', 'test_mypy',
    'test_xmllint', 'test_lint_cmake', 'test_cppcheck', 'test_cpplint',
    'test_uncrustify',
})

#: Directory names that hold tests rather than implementation code.
TEST_DIR_NAMES = frozenset({'test', 'tests'})

#: Python files that are packaging/plumbing, never a package's implementation.
NON_IMPLEMENTATION_FILES = frozenset({
    '__init__.py', 'conftest.py', 'setup.py'})

#: Checked-in per-package non-linter test counts -- the ratchet's floor.
BASELINE_FILENAME = 'test_baseline.json'

#: Environment variable that permits this run to re-cut a floor downwards.
ALLOW_DECREASE_ENV = 'ALLOW_TEST_DECREASE'

#: What to tell someone whose run just tripped the ratchet. The floor rises by
#: itself, so the only thing left to explain is how to lower one on purpose.
BASELINE_HELP = (
    f'the floor rises by itself as tests are added, so lowering it has to be '
    f'deliberate: if the loss is intended, re-run with {ALLOW_DECREASE_ENV}=1 '
    f'(or --allow-decrease) to re-cut the floor down, and commit the result')

#: What to tell someone whose baseline file is missing or unreadable. That is
#: a broken artifact rather than a tripped ratchet, so it wants other advice.
BASELINE_REPAIR_HELP = (
    'restore it from git, or re-create it with `python '
    'scripts/check_test_integrity.py --update-baseline`')

#: What to tell someone whose package.xml will not parse. colcon will not say
#: any of this: it logs the parse error at DEBUG and carries on regardless.
MANIFEST_HELP = (
    'colcon does not fail on a malformed package.xml -- it logs the parse '
    'error at DEBUG, silently reclassifies the package from ament_python to '
    'plain python, and still reports the build as successful, after which the '
    'package produces no test result at all. A literal "--" inside an XML '
    'comment is the usual cause: XML forbids it there.')

#: Header written into the baseline file so it explains itself in a diff.
BASELINE_COMMENT = (
    'Per-package counts of the tests that actually ran and are not ament '
    'linters: the floor scripts/check_test_integrity.py ratchets against. '
    'This file is maintained by `pixi run test`, which raises a floor '
    'whenever a package produces more -- commit it with the change that grew '
    'the suite. Producing FEWER than these numbers (tests deleted, skipped, '
    'or no longer collected) fails `pixi run test` unless the run is told to '
    f'allow it with {ALLOW_DECREASE_ENV}=1 or --allow-decrease.')

#: Counts read from one JUnit XML file. ``sentinel_only`` is True when every
#: test case in the file is colcon's placeholder (see above);
#: ``non_linter`` counts the test cases that are neither linter tests nor
#: skipped.
XUnitCounts = collections.namedtuple(
    'XUnitCounts', 'tests errors failures skipped sentinel_only non_linter')

_STATUS_OK = 'ok'
_STATUS_NO_RESULT = 'no-result'
_STATUS_ZERO_TESTS = 'zero-tests'
_STATUS_ALL_SKIPPED = 'all-skipped'
_STATUS_STALE = 'stale'
_STATUS_BELOW_BASELINE = 'below-baseline'
_STATUS_NO_REAL_TESTS = 'no-real-tests'


class PackageAudit:
    """The verdict on one expected package's test results."""

    def __init__(self, name, *, status, tests=0, errors=0, failures=0,
                 skipped=0, non_linter=0, baseline=None, implementation=(),
                 result_files=(), newest_mtime=None, detail=''):
        """Record a verdict; see :func:`audit_package` for how it is derived."""
        self.name = name
        self.status = status
        self.tests = tests
        self.errors = errors
        self.failures = failures
        self.skipped = skipped
        self.non_linter = non_linter
        self.baseline = baseline
        self.implementation = tuple(implementation)
        self.result_files = list(result_files)
        self.newest_mtime = newest_mtime
        self.detail = detail

    @property
    def executed(self):
        """Return the number of test bodies that actually ran."""
        return self.tests - self.skipped

    @property
    def baseline_delta(self):
        """Return non-linter tests gained/lost vs. baseline, or None."""
        if self.baseline is None:
            return None
        return self.non_linter - self.baseline

    @property
    def ok(self):
        """Return True when this package produced a fresh, non-empty result."""
        return self.status == _STATUS_OK

    def __repr__(self):  # noqa: D105
        return (f'PackageAudit({self.name!r}, status={self.status!r}, '
                f'tests={self.tests})')


def find_manifest_paths(source_dir):
    """Return the sorted ``package.xml`` paths under a tree.

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
        found.append(Path(dirpath) / 'package.xml')
    return sorted(found)


def find_manifests(source_dir):
    """Return ``(name, package.xml path)`` for every package under a tree.

    The expected set is read from the **source tree**, not from whatever
    happens to exist under ``build/``: a package that silently stops being
    tested must still be expected, and therefore still be caught.

    Raises :class:`ValueError`, naming the file and the reason, for a manifest
    that will not parse or carries no ``<name>``. Callers that want to report
    every bad manifest rather than stop at the first want
    :func:`validate_manifests`.
    """
    found = []
    for manifest in find_manifest_paths(source_dir):
        name, problem = read_manifest_name(manifest)
        if problem is not None:
            raise ValueError(f'{manifest}: {problem}')
        found.append((name, manifest))
    return sorted(found)


def read_manifest_name(manifest):
    """Return ``(package name, problem)`` for a ``package.xml``.

    Exactly one of the two is ``None``: a manifest either yields a name or a
    one-line explanation of why it cannot. The XML parse is the interesting
    half -- see :func:`validate_manifests` for why a manifest that does not
    parse has to be caught here rather than left to colcon.
    """
    try:
        root = ElementTree.parse(str(manifest)).getroot()
    except ElementTree.ParseError as error:
        return None, f'is not valid XML -- {error}'
    except OSError as error:
        return None, f'cannot be read -- {error.strerror}'
    element = root.find('name')
    if element is None or not (element.text or '').strip():
        return None, 'has no <name> element'
    return element.text.strip(), None


def validate_manifests(source_dir):
    """Return ``[(path, problem)]`` for every unusable ``package.xml``.

    An empty list means every manifest in the tree parses and names itself.

    This runs ahead of everything else because a malformed manifest breaks
    the gate in a way the gate cannot otherwise see. ``colcon`` reports the
    parse failure at DEBUG level only, quietly falls back to treating the
    directory as a plain ``python`` package instead of an ``ament_python``
    one, and still calls the build successful; the package then produces no
    test result, and the first thing anyone sees is a ``no-result`` audit
    failure that names neither the manifest nor the parse error (D24/D28 --
    ~40 minutes lost to exactly this). A literal ``--`` inside an XML comment
    is the usual cause: XML forbids it there, and nothing else in the
    toolchain says so out loud.
    """
    problems = []
    for manifest in find_manifest_paths(source_dir):
        problem = read_manifest_name(manifest)[1]
        if problem is not None:
            problems.append((manifest, problem))
    return problems


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


def format_manifest_problems(problems):
    """Render the banner printed when a ``package.xml`` will not parse.

    Loud on purpose, and printed instead of the run rather than alongside it:
    the whole failure mode being closed here is one that used to surface as a
    quiet, misattributed symptom several stages later.
    """
    lines = [
        '',
        '=== package.xml validity ' + '=' * 40,
    ]
    for path, problem in problems:
        lines.append(f'FAIL {path} {problem}')
    lines.append('-' * 65)
    lines.append(
        f'MANIFEST CHECK FAILED: {len(problems)} package.xml file(s) are not '
        f'usable; nothing else was run')
    lines.append(f'note: {MANIFEST_HELP}')
    lines.append('')
    return '\n'.join(lines)


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
    # A result that reports counts without listing its cases (colcon's own
    # placeholder, a hand-written summary) says nothing about *which* tests
    # ran, so credit every test it does claim to have run rather than invent
    # a shortfall -- but its skipped count is still a count of tests that did
    # not run, so it is subtracted here exactly as the per-case skips are.
    non_linter = (
        sum(1 for case in cases
            if not is_linter_case(case) and not is_skipped_case(case))
        if cases else max(tests - skipped, 0))
    return XUnitCounts(
        tests, errors, failures, skipped, sentinel_only, non_linter)


def is_linter_case(case):
    """Return True when a JUnit ``<testcase>`` is one of the linter tests.

    Linter tests are real tests -- they are why every package carries an
    ``ament_lint`` test_depend -- but they exist whether or not the package
    has any code, so they must not be counted as evidence that the package's
    *behaviour* is tested. Parametrised names (``test_flake8[x]``) are matched
    on their base name.
    """
    name = (case.get('name') or '').split('[')[0]
    module = (case.get('classname') or '').split('.')[-1]
    return name in LINTER_TEST_NAMES or module in LINTER_TEST_NAMES


def is_skipped_case(case):
    """Return True when a JUnit ``<testcase>`` records a test that never ran.

    A skipped test is not evidence about the code, so the ratchet must not
    accept one in place of the test it used to be: ``@pytest.mark.skip`` on
    ten tests removes exactly as much coverage as deleting them, and leaves
    the *collected* count untouched, so counting collections would let a suite
    be hollowed out without tripping anything. Discounting them here means a
    skip and a deletion trip the same floor.
    """
    return any(child.tag in SKIPPED_CASE_TAGS for child in case)


def find_implementation_modules(package_dir):
    """Return a package's implementation modules, relative to its root.

    "Implementation code" is read narrowly on purpose: python modules inside
    an importable subpackage (a directory holding an ``__init__.py``) of the
    package root, skipping test directories, ``__init__.py``/``conftest.py``/
    ``setup.py``, and files that hold nothing but blank lines and comments.
    A skeleton package -- one empty ``__init__.py`` and nothing else -- is
    therefore honestly classified as having no implementation, so its three
    linter tests remain an acceptable suite; the first real module to land in
    it (a clamp function in ``robot_safety``, say) flips that immediately.
    """
    package_dir = Path(package_dir)
    if not package_dir.is_dir():
        return ()
    modules = []
    for child in sorted(package_dir.iterdir()):
        if (not child.is_dir() or child.name in TEST_DIR_NAMES
                or child.name.startswith('.')
                or not (child / '__init__.py').is_file()):
            continue
        for path in sorted(child.rglob('*.py')):
            relative = path.relative_to(child)
            if (path.name in NON_IMPLEMENTATION_FILES
                    or set(relative.parts) & TEST_DIR_NAMES):
                continue
            if _holds_code(path):
                modules.append(str(path.relative_to(package_dir)))
    return tuple(modules)


def _holds_code(path):
    """Return True when a python file holds more than blanks and comments."""
    try:
        text = path.read_text(encoding='utf-8', errors='replace')
    except OSError:
        return False
    return any(line.strip() and not line.lstrip().startswith('#')
               for line in text.splitlines())


def implementation_map(source_dir, packages):
    """Return ``{package: implementation modules}`` for ``packages``."""
    wanted = set(packages)
    return {name: find_implementation_modules(manifest.parent)
            for name, manifest in find_manifests(source_dir)
            if name in wanted}


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


def audit_package(name, build_base, *, min_mtime=None, baseline=None,
                  implementation=()):
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

    Two further rules act on the *non-linter, non-skipped* count (see
    :func:`is_linter_case` and :func:`is_skipped_case`). A package listing
    ``implementation`` modules (:func:`find_implementation_modules`) must have
    at least one such test, so code cannot land behind a suite of nothing but
    linters and skips. And when ``baseline`` is given, producing fewer of them
    than it records fails: the ratchet that catches a suite quietly shrinking,
    whether the tests were deleted, stopped being collected, or were skipped
    where they used to run.
    """
    directory = Path(build_base) / name
    files = find_result_files(directory) if directory.is_dir() else []
    common = {'baseline': baseline, 'implementation': implementation}
    if not files:
        return PackageAudit(
            name, status=_STATUS_NO_RESULT, **common,
            detail=f'no JUnit result file under {directory}')

    newest = max(f.stat().st_mtime for f in files)
    if min_mtime is not None:
        fresh = [f for f in files
                 if f.stat().st_mtime >= min_mtime - MTIME_TOLERANCE]
        if not fresh:
            return PackageAudit(
                name, status=_STATUS_STALE, result_files=files, **common,
                newest_mtime=newest,
                detail='only stale results ({}, newest {:.0f}s before this '
                       'run)'.format(_join(files), min_mtime - newest))
        files = fresh
        newest = max(f.stat().st_mtime for f in files)

    tests = errors = failures = skipped = non_linter = 0
    sentinel_only = True
    for path in files:
        parsed = parse_xunit(path)
        tests += parsed.tests
        errors += parsed.errors
        failures += parsed.failures
        skipped += parsed.skipped
        non_linter += parsed.non_linter
        sentinel_only = sentinel_only and parsed.sentinel_only

    verdict = PackageAudit(
        name, status=_STATUS_OK, tests=tests, errors=errors,
        failures=failures, skipped=skipped, non_linter=non_linter,
        result_files=files, newest_mtime=newest, **common)
    if sentinel_only:
        verdict.status = _STATUS_NO_RESULT
        verdict.tests = verdict.skipped = verdict.non_linter = 0
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
    elif verdict.implementation and non_linter == 0:
        verdict.status = _STATUS_NO_REAL_TESTS
        verdict.detail = (
            f'holds implementation code ({_join_names(verdict.implementation)})'
            f' but every one of its {tests} collected tests is a linter test '
            f'or was skipped: nothing tests what this package does')
    elif baseline is not None and non_linter < baseline:
        verdict.status = _STATUS_BELOW_BASELINE
        verdict.detail = (
            f'{non_linter} non-linter tests ran, {baseline - non_linter} '
            f'below the baseline of {baseline}: tests were removed, skipped, '
            f'or stopped being collected -- {BASELINE_HELP}')
    return verdict


def _join_names(names, limit=3):
    """Render a few names, with a count of whatever is left over."""
    names = list(names)
    shown = ', '.join(names[:limit])
    return shown if len(names) <= limit else f'{shown}, +{len(names) - limit}'


def _join(paths):
    return ', '.join(str(p) for p in paths)


def audit(packages, build_base, *, min_mtime=None, baseline=None,
          implementations=None):
    """Audit every expected package and return the list of verdicts."""
    baseline = baseline or {}
    implementations = implementations or {}
    return [audit_package(name, build_base, min_mtime=min_mtime,
                          baseline=baseline.get(name),
                          implementation=implementations.get(name, ()))
            for name in packages]


def load_baseline(path):
    """Return ``{package: non-linter test count}`` from a baseline file.

    Raises :class:`ValueError` when the file is missing or malformed rather
    than falling back to "no baseline": a floor that quietly evaporates --
    deleted, renamed, corrupted -- would silently switch the ratchet off,
    which is precisely the class of failure this guard exists to catch.
    """
    path = Path(path)
    try:
        data = json.loads(path.read_text(encoding='utf-8'))
    except OSError as error:
        raise ValueError(
            f'{path}: baseline file cannot be read ({error.strerror}); '
            f'{BASELINE_REPAIR_HELP}')
    except json.JSONDecodeError as error:
        raise ValueError(f'{path}: baseline file is not valid JSON ({error})')
    packages = data.get('packages') if isinstance(data, dict) else None
    if not isinstance(packages, dict):
        raise ValueError(
            f'{path}: baseline file has no "packages" object; '
            f'{BASELINE_REPAIR_HELP}')
    counts = {}
    for name, value in packages.items():
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise ValueError(
                f'{path}: baseline for {name} is not a non-negative integer: '
                f'{value!r}')
        counts[name] = value
    return counts


def write_baseline(path, counts):
    """Write ``counts`` as the baseline file, sorted for a legible diff."""
    payload = {
        'comment': BASELINE_COMMENT,
        'packages': {name: counts[name] for name in sorted(counts)},
    }
    Path(path).write_text(json.dumps(payload, indent=2) + '\n',
                          encoding='utf-8')


def baseline_updates(baseline, audits, *, allow_decrease=False):
    """Return ``{package: new floor}`` for the entries a run should rewrite.

    The ratchet only turns one way on its own. A package that produced *more*
    non-linter tests than its floor records raises it, and a package with no
    entry gets one, so growing a suite records its own new floor and there is
    no manual step to forget. A package that produced *fewer* is the failure
    the floor exists to catch, and is rewritten only when ``allow_decrease``
    says the loss was deliberate; blindly re-cutting to the current count
    would turn the guard into a rubber stamp -- a package collapsing from 61
    tests to 3 would simply record 3 and pass.
    """
    updates = {}
    for verdict in sorted(audits, key=lambda a: a.name):
        old = baseline.get(verdict.name)
        if old is None or verdict.non_linter > old:
            updates[verdict.name] = verdict.non_linter
        elif allow_decrease and verdict.non_linter < old:
            updates[verdict.name] = verdict.non_linter
    return updates


def baseline_blockers(audits, *, allow_decrease=False):
    """Return the verdicts that make a run unfit to re-cut a floor from.

    A floor is a claim about how much testing this workspace really does, so
    it may only be cut from a run that actually did it. Anything short of a
    usable result -- no result file, zero tests, an all-skipped suite, stale
    evidence, implementation code with no real test -- would pin the wrong
    number, and so would a run whose tests errored or failed: an error during
    collection silently costs a module's worth of tests, and a red suite is
    not a measurement of anything. Being *below* the floor is the one
    exception, and only when ``allow_decrease`` makes it the point of the run.
    """
    usable = {_STATUS_OK}
    if allow_decrease:
        usable.add(_STATUS_BELOW_BASELINE)
    return [a for a in sorted(audits, key=lambda a: a.name)
            if a.status not in usable or a.errors or a.failures]


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


def format_baseline_delta(verdict):
    """Render one package's non-linter delta vs. its baseline entry."""
    if verdict.baseline is None:
        return '-'
    return f'{verdict.baseline_delta:+d}'


def format_report(audits, *, notes=(), show_age=False, tolerated=()):
    """Render the per-package summary printed on both success and failure.

    ``tolerated`` names statuses that must not be reported as failures -- a
    run allowed to lower a floor uses it so the count it is about to re-cut is
    shown as movement rather than as a verdict.
    """
    width = max([len(a.name) for a in audits] + [len('package')])
    status_width = max([len(a.status) for a in audits] + [len('status')])
    header = (f'{"package".ljust(width)}  tests  skipped  errors  failures'
              f'  non-lint  vs-base  {"status".ljust(status_width)}')
    if show_age:
        header += f'  {"age":>6}'
    lines = [
        '',
        '=== test integrity audit ' + '=' * max(0, len(header) - 25),
        header.rstrip(),
    ]
    now = time.time()
    for a in sorted(audits, key=lambda a: a.name):
        row = (f'{a.name.ljust(width)}  {a.tests:5d}  {a.skipped:7d}  '
               f'{a.errors:6d}  {a.failures:8d}  {a.non_linter:8d}  '
               f'{format_baseline_delta(a):>7}  '
               f'{a.status.ljust(status_width)}')
        if show_age:
            age = ('-' if a.newest_mtime is None
                   else format_age(now - a.newest_mtime))
            row += f'  {age:>6}'
        lines.append(row.rstrip())
    lines.append('-' * len(header))
    total = sum(a.tests for a in audits)
    total_skipped = sum(a.skipped for a in audits)
    total_non_linter = sum(a.non_linter for a in audits)
    summary = f'{len(audits)} packages, {total} tests collected'
    if total_skipped:
        summary += f' ({total_skipped} skipped)'
    summary += f', {total_non_linter} of them non-linter'
    lines.append(summary)
    lines.extend(notes)

    bad = [a for a in audits if not a.ok and a.status not in tolerated]
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


def _baseline_notes(baseline, packages, narrowed, error, updating=False):
    """Render the baseline-related lines appended to the report.

    A package with no baseline entry, or an entry for a package that is no
    longer here, is reported but never fatal: adding a package must not fail
    the run before it has a floor, and the "implementation code needs real
    tests" rule already covers the case that matters. A run that is about to
    write the file fixes both, so it says nothing.
    """
    if error is not None:
        return [f'error: {error}']
    if updating:
        return []
    notes = [
        f'note: {name} has no entry in the test-count baseline (new '
        f'package?), so nothing ratchets it yet; a full `pixi run test` '
        f'records one automatically'
        for name in packages if name not in baseline]
    if narrowed:
        return notes
    return notes + [
        f'note: the test-count baseline still lists {name}, which is not in '
        f'this workspace (renamed or removed?); prune it with `python '
        f'scripts/check_test_integrity.py --update-baseline`'
        for name in sorted(set(baseline) - set(packages))]


def _env_flag(name, environ=None):
    """Return True when environment variable ``name`` is set to a yes.

    Read permissively (``1``/``true``/``yes``/``on``, any case) because this
    is a human typing a prefix on a command line, but *not* so permissively
    that ``ALLOW_TEST_DECREASE=0`` -- the obvious way to write "no" -- quietly
    lowers a floor.
    """
    value = (environ if environ is not None else os.environ).get(name, '')
    return value.strip().lower() in ('1', 'true', 'yes', 'on')


def _is_own_workspace(source_dir, repo_root):
    """Return True when ``source_dir`` is this repository's own ``src/``."""
    try:
        return Path(source_dir).resolve() == (Path(repo_root) / 'src').resolve()
    except OSError:
        return False


def _maintain_baseline(path, baseline, audits, *, allow_decrease=False,
                       prune=False, explicit=False):
    """Ratchet the baseline from ``audits``; return 0 on success, 1 if not.

    This is what makes the floor self-maintaining: every full run rewrites it
    upwards to what the run just produced, so a PR that adds tests carries the
    matching floor bump in its own diff instead of relying on someone
    remembering ``--update-baseline`` (nobody did, and ``robot_world`` drifted
    eleven tests above its recorded floor). Only :func:`baseline_updates`
    decides what moves; this function decides *whether* anything may move,
    prints the movement, and writes the file.

    A run with :func:`baseline_blockers` writes nothing. When the caller asked
    for the update in so many words that refusal is an error; when the ratchet
    was merely doing its automatic housekeeping it is not, because the thing
    that blocked it has already failed the run on its own and a second
    complaint would only bury the first. Either way the file is left alone, so
    a red run never leaves a rewritten baseline behind in the tree.

    ``prune`` drops entries for packages the run did not cover, which is
    right for a whole-workspace ``--update-baseline`` (the package is gone)
    and wrong for a narrowed one (the package was merely not selected) -- and
    wrong for the automatic path, where an entry with no result is a package
    that failed the audit, not a package that left.
    """
    updates = baseline_updates(baseline, audits, allow_decrease=allow_decrease)
    blockers = baseline_blockers(audits, allow_decrease=allow_decrease)
    if blockers:
        if explicit or updates:
            print('not updating the test-count baseline: this run is not '
                  'otherwise green (' +
                  ', '.join(f'{a.name} ({a.status})' for a in blockers) + ')')
        return 1 if explicit else 0

    counts = dict(baseline)
    counts.update(updates)
    if prune:
        covered = {a.name for a in audits}
        counts = {name: count for name, count in counts.items()
                  if name in covered}
    if counts == baseline:
        return 0
    try:
        write_baseline(path, counts)
    except OSError as error:
        print(f'error: cannot write the test-count baseline {path} '
              f'({error.strerror})')
        return 1
    for name, new in sorted(updates.items()):
        old = baseline.get(name)
        movement = 'new' if old is None else (
            'raised' if new > old else 'LOWERED')
        print(f'baseline {name}: {"-" if old is None else old} -> {new} '
              f'({movement})')
    for name in sorted(set(baseline) - set(counts)):
        print(f'baseline {name}: {baseline[name]} -> dropped (no such '
              f'package in this workspace)')
    print(f'wrote {path}; commit it with this change')
    return 0


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
    parser.add_argument(
        '--baseline', default=None,
        help=f'per-package non-linter test-count floor. Defaults to the '
             f'checked-in scripts/{BASELINE_FILENAME}, which describes THIS '
             f"repository's workspace, so it is used only when --source-dir "
             f"is this repository's src/; point a run at another source tree "
             f'and the ratchet is inert unless you name its own baseline')
    parser.add_argument(
        '--allow-decrease', action='store_true',
        default=_env_flag(ALLOW_DECREASE_ENV),
        help=f'permit this run to re-cut a floor DOWNWARDS: a package below '
             f'its baseline stops being a failure and its entry is rewritten '
             f'to the lower count. The deliberate act of recording tests that '
             f'were legitimately removed; raising a floor needs no flag. Also '
             f'settable as {ALLOW_DECREASE_ENV}=1 in the environment')
    parser.add_argument(
        '--update-baseline', action='store_true',
        help='--allow-decrease, plus drop entries for packages this run did '
             'not cover: the way to re-cut the whole file after a package is '
             'renamed or removed, and the only mode that writes the baseline '
             'in --audit-only. Refuses to write from a run whose packages did '
             'not all produce a usable result')
    args = parser.parse_args(argv)
    allow_decrease = args.allow_decrease or args.update_baseline

    if not Path(args.source_dir).is_dir():
        parser.error(f'--source-dir is not a directory: {args.source_dir}')

    # First, ahead of the audit and the ratchet and (in the driver) ahead of
    # colcon itself: a manifest that does not parse makes every later verdict
    # a report about a workspace that is not the one on disk, so it fails the
    # run here, on its own, naming the file and the parse error.
    manifest_problems = validate_manifests(args.source_dir)
    if manifest_problems:
        print(format_manifest_problems(manifest_problems), flush=True)
        return 1

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

    # The checked-in baseline describes this repository's own workspace, so a
    # run pointed at some other source tree (a fixture, a scratch workspace)
    # ratchets only against a baseline it was explicitly given -- otherwise
    # this repo's counts would be applied to packages they say nothing about.
    baseline_path = args.baseline
    if baseline_path is None and _is_own_workspace(args.source_dir, repo_root):
        baseline_path = str(Path(__file__).resolve().parent
                            / BASELINE_FILENAME)
    if baseline_path is None and args.update_baseline:
        parser.error(
            '--update-baseline needs --baseline: --source-dir is not this '
            f"repository's src/, so scripts/{BASELINE_FILENAME} does not "
            f'describe it')

    baseline, baseline_error = {}, None
    if baseline_path is not None:
        try:
            baseline = load_baseline(baseline_path)
        except ValueError as error:
            # Bootstrapping (or repairing) the file is exactly what
            # --update-baseline is for, so only a ratcheting run fails on it.
            baseline_error = None if args.update_baseline else str(error)

    implementations = implementation_map(args.source_dir, packages)
    if TOOLING_PACKAGE in packages:
        # The pseudo-package's implementation is this very script, and the
        # suite that tests it is the reason the whole guard can be trusted.
        implementations[TOOLING_PACKAGE] = (Path(__file__).name,)

    notes = _notes(narrowed, packages, unowned, args)
    if baseline_path is not None:
        notes += _baseline_notes(baseline, packages, narrowed, baseline_error,
                                 updating=args.update_baseline)
    # The automatic ratchet only ever runs in the full driver: --audit-only
    # re-reads whatever XML happens to be lying in the build tree, which is
    # evidence about some earlier run rather than about this tree, and a floor
    # is too load-bearing to cut from that. Asking for it in so many words
    # (--update-baseline) still works there, for repairing the file by hand.
    maintaining = baseline_path is not None and not baseline_error and (
        args.update_baseline or not args.audit_only)
    # A shortfall this run is about to record is movement, not a verdict, so
    # it must not also be reported as a failure. A run that is *not* going to
    # record it keeps the failure, so `--audit-only` cannot be made to swallow
    # a shortfall it has no way of writing down.
    tolerated = frozenset(
        [_STATUS_BELOW_BASELINE] if allow_decrease and maintaining else [])

    if args.audit_only:
        audits = audit(packages, args.build_base, baseline=baseline,
                       implementations=implementations)
        print('*** --audit-only: nothing was re-run; the ages below are how '
              'old this evidence is ***', flush=True)
        print(format_report(audits, notes=notes, show_age=True,
                            tolerated=tolerated))
        rc = 0 if all(a.ok or a.status in tolerated for a in audits) else 1
        if maintaining:
            rc |= _maintain_baseline(
                baseline_path, baseline, audits, allow_decrease=allow_decrease,
                prune=not narrowed, explicit=True)
        return 1 if (rc or baseline_error) else 0

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

    audits = audit(packages, args.build_base, min_mtime=started,
                   baseline=baseline, implementations=implementations)
    print(format_report(audits, notes=notes, tolerated=tolerated))
    rc_audit = 0 if all(a.ok or a.status in tolerated for a in audits) else 1

    rc_baseline = 1 if baseline_error else 0
    if baseline_error:
        print(f'error: {baseline_error}')
    if maintaining:
        rc_baseline |= _maintain_baseline(
            baseline_path, baseline, audits, allow_decrease=allow_decrease,
            prune=args.update_baseline and not narrowed,
            explicit=args.update_baseline)

    stages = {
        'colcon test': rc_test,
        'workspace-tooling tests': rc_tooling,
        'colcon test-result': rc_result,
        'test integrity audit': rc_audit,
        'test-count baseline': rc_baseline,
    }
    failed = [name for name, rc in stages.items() if rc]
    if failed:
        print('FAILED stages: ' + ', '.join(failed))
        return 1
    print('All stages passed.')
    return 0


if __name__ == '__main__':
    sys.exit(main())
