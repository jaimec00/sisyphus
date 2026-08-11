# Copyright (c) 2026 Jaime C.
#
# Use of this source code is governed by an MIT-style
# license that can be found in the LICENSE file or at
# https://opensource.org/licenses/MIT.

"""Tests for the test-count ratchet in ``scripts/check_test_integrity.py``.

"More than zero tests" is a floor low enough to walk under: a package can
lose 56 of its 59 tests and still clear it, and a package can grow real code
behind a suite of nothing but ament linters. These tests cover the two rules
that close those gaps -- the per-package baseline and the
implementation-code-needs-real-tests check -- plus the ``--update-baseline``
path that keeps the baseline maintainable rather than a footgun.
"""

import json
from pathlib import Path

import check_test_integrity as guard
import pytest
from test_audit import write_result, write_source_package

REPO_ROOT = Path(__file__).resolve().parents[2]


def write_implementation(package_dir, name='thing.py', body='VALUE = 1\n'):
    """Add an importable subpackage holding one module, as a ROS package has."""
    package_dir = Path(package_dir)
    source = package_dir / package_dir.name
    source.mkdir(parents=True, exist_ok=True)
    (source / '__init__.py').write_text('')
    (source / name).write_text(body)
    return source / name


def write_skeleton(package_dir):
    """Add the empty ``__init__.py``-only tree the skeleton packages ship."""
    package_dir = Path(package_dir)
    source = package_dir / package_dir.name
    source.mkdir(parents=True, exist_ok=True)
    (source / '__init__.py').write_text('')
    return source


def write_baseline_file(path, counts):
    """Write a baseline file the way ``--update-baseline`` would."""
    guard.write_baseline(path, counts)
    return path


@pytest.fixture
def workspace(tmp_path):
    """Return an empty ``(source_dir, build_base)`` pair."""
    source_dir = tmp_path / 'src'
    build_base = tmp_path / 'build'
    source_dir.mkdir()
    build_base.mkdir()
    return source_dir, build_base


# --- counting: linter tests are not evidence that behaviour is tested -------

def test_linter_tests_are_not_counted_as_real_tests(workspace):
    """The three ament linters must not stand in for testing the package."""
    _, build_base = workspace
    write_result(build_base, 'robot_x', tests=3, linters=3)

    verdict = guard.audit_package('robot_x', build_base)

    assert verdict.tests == 3
    assert verdict.non_linter == 0


def test_real_tests_are_counted_alongside_the_linters(workspace):
    """A normal package: linters plus its own tests, only the latter count."""
    _, build_base = workspace
    write_result(build_base, 'robot_x', tests=62, linters=3)

    verdict = guard.audit_package('robot_x', build_base)

    assert (verdict.tests, verdict.non_linter) == (62, 59)


@pytest.mark.parametrize('classname, name', [
    ('robot_x.test.test_flake8', 'test_flake8'),
    ('robot_x.test.test_copyright', 'test_copyright'),
    ('robot_x.test.test_pep257', 'test_pep257'),
    # scripts/tests/test_lint.py runs the same linters from a module whose
    # name matches no linter: the test's own name still gives it away.
    ('scripts.tests.test_lint', 'test_flake8'),
    # Parametrised linter tests are matched on their base name.
    ('robot_x.test.test_flake8', 'test_flake8[src]'),
])
def test_every_shape_of_linter_case_is_recognised(classname, name):
    """Both the module name and the test name identify a linter test."""
    case = ElementTreeCase(classname, name)

    assert guard.is_linter_case(case)


@pytest.mark.parametrize('classname, name', [
    ('robot_x.test.test_skills', 'test_grasp_rejects_a_far_pose'),
    # A test *about* linting the workspace is a real test of real behaviour.
    ('robot_x.test.test_config', 'test_flake8_config_is_shipped'),
])
def test_ordinary_tests_are_not_mistaken_for_linters(classname, name):
    """The linter set must not swallow tests that exercise the package."""
    assert not guard.is_linter_case(ElementTreeCase(classname, name))


class ElementTreeCase:
    """The two attributes :func:`guard.is_linter_case` reads off a testcase."""

    def __init__(self, classname, name):
        """Record the ``classname``/``name`` pair under test."""
        self._attributes = {'classname': classname, 'name': name}

    def get(self, key):
        """Return an attribute the way ``ElementTree.Element`` does."""
        return self._attributes.get(key)


def test_a_result_without_test_cases_credits_its_reported_count(workspace):
    """No ``<testcase>`` elements means no evidence of a *shortfall*."""
    _, build_base = workspace
    directory = build_base / 'robot_x'
    directory.mkdir()
    (directory / 'pytest.xml').write_text(
        '<?xml version="1.0"?><testsuite name="p" tests="7" failures="0"/>')

    verdict = guard.audit_package('robot_x', build_base, baseline=7)

    assert verdict.ok
    assert verdict.non_linter == 7


# --- the ratchet -----------------------------------------------------------

def test_a_package_that_loses_tests_fails(workspace):
    """The headline case from the issue: 59 tests silently become 3 linters."""
    _, build_base = workspace
    write_result(build_base, 'robot_x', tests=3, linters=3)

    verdict = guard.audit_package('robot_x', build_base, baseline=56)

    assert not verdict.ok
    assert verdict.status == 'below-baseline'
    assert '0 non-linter tests, 56 below the baseline of 56' in verdict.detail
    assert '--update-baseline' in verdict.detail, 'the fix must be legible'


def test_holding_the_baseline_exactly_passes(workspace):
    """The ratchet is a floor, not an equality check."""
    _, build_base = workspace
    write_result(build_base, 'robot_x', tests=62, linters=3)

    assert guard.audit_package('robot_x', build_base, baseline=59).ok


def test_gaining_tests_passes_and_is_reported(workspace):
    """Adding tests must never fail a run, but must be visible."""
    _, build_base = workspace
    write_result(build_base, 'robot_x', tests=70, linters=3)

    verdict = guard.audit_package('robot_x', build_base, baseline=59)

    assert verdict.ok
    assert verdict.baseline_delta == 8
    assert '+8' in guard.format_report([verdict])


def test_losing_only_linter_tests_does_not_trip_the_ratchet(workspace):
    """The floor is about behaviour tests; linters are counted separately."""
    _, build_base = workspace
    write_result(build_base, 'robot_x', tests=59, linters=0)

    assert guard.audit_package('robot_x', build_base, baseline=59).ok


def test_a_skipped_test_is_still_collected_and_does_not_trip_the_ratchet(
        workspace):
    """Skips are the all-skipped rule's business, not the ratchet's.

    A hardware- or dependency-gated skip must not turn into a spurious
    ratchet failure on a machine that legitimately cannot run it.
    """
    _, build_base = workspace
    write_result(build_base, 'robot_x', tests=59, skipped=10)

    verdict = guard.audit_package('robot_x', build_base, baseline=59)

    assert verdict.ok
    assert (verdict.non_linter, verdict.executed) == (59, 49)


def test_the_delta_is_printed_for_every_package(workspace):
    """Every run prints the per-package movement, passing or failing."""
    _, build_base = workspace
    write_result(build_base, 'robot_up', tests=10)
    write_result(build_base, 'robot_flat', tests=5)
    write_result(build_base, 'robot_new', tests=5)

    report = guard.format_report([
        guard.audit_package('robot_up', build_base, baseline=5),
        guard.audit_package('robot_flat', build_base, baseline=5),
        guard.audit_package('robot_new', build_base),
    ])
    rows = {line.split()[0]: line.split() for line in report.splitlines()
            if line.startswith('robot_')}

    assert rows['robot_up'][-2] == '+5'
    assert rows['robot_flat'][-2] == '+0'
    assert rows['robot_new'][-2] == '-', 'no baseline entry, no delta'


def test_a_package_with_no_baseline_entry_is_not_ratcheted(workspace):
    """A newly added package must not fail before its first baseline cut."""
    _, build_base = workspace
    write_result(build_base, 'robot_x', tests=4)

    assert guard.audit_package('robot_x', build_base, baseline=None).ok


# --- implementation code must have real tests ------------------------------

def test_implementation_code_with_only_linter_tests_fails(workspace):
    """The rule that catches robot_safety growing a clamp with no test."""
    source_dir, build_base = workspace
    package = write_source_package(source_dir, 'robot_safety')
    write_implementation(package, 'clamp.py', 'def clamp(v):\n    return v\n')
    write_result(build_base, 'robot_safety', tests=3, linters=3)

    verdict = guard.audit_package(
        'robot_safety', build_base,
        implementation=guard.find_implementation_modules(package))

    assert not verdict.ok
    assert verdict.status == 'no-real-tests'
    assert 'clamp.py' in verdict.detail


def test_one_real_test_satisfies_the_implementation_rule(workspace):
    """The rule is a floor of one, not a coverage target."""
    source_dir, build_base = workspace
    package = write_source_package(source_dir, 'robot_safety')
    write_implementation(package, 'clamp.py', 'def clamp(v):\n    return v\n')
    write_result(build_base, 'robot_safety', tests=4, linters=3)

    verdict = guard.audit_package(
        'robot_safety', build_base,
        implementation=guard.find_implementation_modules(package))

    assert verdict.ok


def test_a_skeleton_package_passes_on_its_linters(workspace):
    """Three linter tests are an honest suite for a package with no code."""
    source_dir, build_base = workspace
    package = write_source_package(source_dir, 'robot_brain')
    write_skeleton(package)
    write_result(build_base, 'robot_brain', tests=3, linters=3)

    verdict = guard.audit_package(
        'robot_brain', build_base,
        implementation=guard.find_implementation_modules(package))

    assert verdict.ok
    assert verdict.implementation == ()


# --- what counts as implementation code ------------------------------------

def test_implementation_modules_are_found_including_nested_ones(workspace):
    """Real modules -- at any depth under the source dir -- are implementation."""
    source_dir, _ = workspace
    package = write_source_package(source_dir, 'robot_x')
    write_implementation(package, 'thing.py')
    nested = package / 'robot_x' / 'nested'
    nested.mkdir()
    (nested / '__init__.py').write_text('')
    (nested / 'deep.py').write_text('X = 2\n')

    modules = guard.find_implementation_modules(package)

    assert set(modules) == {'robot_x/thing.py', 'robot_x/nested/deep.py'}


@pytest.mark.parametrize('relative, body', [
    ('robot_x/__init__.py', 'from robot_x.thing import VALUE\n'),
    ('robot_x/conftest.py', 'import pytest\n'),
    ('setup.py', 'from setuptools import setup\nsetup()\n'),
    ('test/test_thing.py', 'def test_thing():\n    pass\n'),
    ('robot_x/test/test_inner.py', 'def test_inner():\n    pass\n'),
    ('robot_x/empty.py', ''),
    ('robot_x/notes.py', '# just a comment\n\n'),
    ('resource/robot_x.py', 'X = 1\n'),
])
def test_packaging_tests_and_empty_files_are_not_implementation(
        workspace, relative, body):
    """Stay conservative: only real code in an importable subpackage counts.

    Every path here exists in the workspace today, so a false positive on any
    of them would fail packages that are honestly tested (or honestly empty).
    """
    source_dir, _ = workspace
    package = write_source_package(source_dir, 'robot_x')
    write_skeleton(package)
    path = package / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body)

    assert guard.find_implementation_modules(package) == ()


def test_the_real_skeleton_packages_are_still_read_as_skeletons():
    """Guard the conservatism claim against the actual workspace.

    If this fails, either a skeleton package grew code (and now owes real
    tests) or the detection became too eager -- both worth stopping for.
    """
    source_dir = REPO_ROOT / 'src'
    modules = guard.implementation_map(
        source_dir, guard.find_source_packages(source_dir))

    for name in ('robot_brain', 'robot_bringup', 'robot_description',
                 'robot_perception'):
        assert modules.get(name) == (), f'{name} is no longer a skeleton'
    assert modules['robot_skills'], 'robot_skills holds implementation code'
    assert modules['robot_backends'], 'robot_backends holds implementation'
    assert modules['robot_safety'], 'robot_safety holds the clamp layer'


# --- the baseline file -----------------------------------------------------

def test_the_checked_in_baseline_covers_every_package_this_repo_owns():
    """A package missing from the baseline is a package nothing ratchets."""
    baseline = guard.load_baseline(
        REPO_ROOT / 'scripts' / guard.BASELINE_FILENAME)
    packages = set(guard.find_source_packages(REPO_ROOT / 'src'))
    packages.add(guard.TOOLING_PACKAGE)

    assert packages <= set(baseline), (
        'run --update-baseline and commit: ' + str(packages - set(baseline)))


def test_a_baseline_round_trips_and_is_sorted(tmp_path):
    """The file is a reviewed artifact, so its diffs must stay legible."""
    path = tmp_path / 'baseline.json'

    guard.write_baseline(path, {'robot_b': 2, 'robot_a': 1})
    data = json.loads(path.read_text())

    assert list(data['packages']) == ['robot_a', 'robot_b']
    assert '--update-baseline' in data['comment']
    assert guard.load_baseline(path) == {'robot_a': 1, 'robot_b': 2}


@pytest.mark.parametrize('body', [
    '{"packages": {"robot_a": "many"}}',
    '{"packages": {"robot_a": -1}}',
    '{"packages": {"robot_a": true}}',
    '{"packages": []}',
    '{"counts": {"robot_a": 1}}',
    'not json at all',
])
def test_a_malformed_baseline_is_an_error_not_an_empty_floor(tmp_path, body):
    """A baseline that cannot be read must never silently mean "no floor"."""
    path = tmp_path / 'baseline.json'
    path.write_text(body)

    with pytest.raises(ValueError):
        guard.load_baseline(path)


def test_a_missing_baseline_is_an_error(tmp_path):
    """Deleting the file must not be a way to switch the ratchet off."""
    with pytest.raises(ValueError):
        guard.load_baseline(tmp_path / 'nope.json')


# --- the CLI: ratcheting, updating, and staying out of the way -------------

def audit_only(source_dir, build_base, *argv):
    """Run the guard's audit-only mode over a fixture workspace."""
    return guard.main(['--audit-only', '--source-dir', str(source_dir),
                       '--build-base', str(build_base), *argv])


def test_the_cli_fails_and_names_the_package_that_lost_tests(
        workspace, capsys, tmp_path):
    """End to end: a shrunken suite fails the run with a legible reason."""
    source_dir, build_base = workspace
    write_source_package(source_dir, 'robot_a')
    write_result(build_base, 'robot_a', tests=3, linters=3)
    write_result(build_base, guard.TOOLING_PACKAGE, tests=3)
    baseline = write_baseline_file(
        tmp_path / 'baseline.json',
        {'robot_a': 59, guard.TOOLING_PACKAGE: 3})

    rc = audit_only(source_dir, build_base, '--baseline', str(baseline))
    out = capsys.readouterr().out

    assert rc == 1
    assert 'FAIL robot_a' in out and 'below the baseline of 59' in out


def test_a_run_against_another_source_tree_does_not_use_this_baseline(
        workspace, capsys):
    """This repo's counts say nothing about someone else's workspace."""
    source_dir, build_base = workspace
    write_source_package(source_dir, 'robot_a')
    write_result(build_base, 'robot_a', tests=1)
    write_result(build_base, guard.TOOLING_PACKAGE, tests=3)

    rc = audit_only(source_dir, build_base)
    out = capsys.readouterr().out

    assert rc == 0, 'the checked-in baseline must not leak into a fixture run'
    assert 'below-baseline' not in out


def test_a_missing_baseline_fails_the_run_rather_than_disabling_the_ratchet(
        workspace, capsys, tmp_path):
    """The floor evaporating is itself a failure, and says how to fix it."""
    source_dir, build_base = workspace
    write_source_package(source_dir, 'robot_a')
    write_result(build_base, 'robot_a', tests=5)
    write_result(build_base, guard.TOOLING_PACKAGE, tests=3)

    rc = audit_only(source_dir, build_base,
                    '--baseline', str(tmp_path / 'gone.json'))
    out = capsys.readouterr().out

    assert rc == 1
    assert 'baseline file cannot be read' in out
    assert '--update-baseline' in out


def test_update_baseline_records_the_current_counts(workspace, capsys,
                                                    tmp_path):
    """The documented escape hatch: re-cut the floor, then commit it."""
    source_dir, build_base = workspace
    write_source_package(source_dir, 'robot_a')
    write_result(build_base, 'robot_a', tests=12, linters=3)
    write_result(build_base, guard.TOOLING_PACKAGE, tests=3)
    baseline = write_baseline_file(tmp_path / 'baseline.json',
                                   {'robot_a': 59, guard.TOOLING_PACKAGE: 3})

    rc = audit_only(source_dir, build_base, '--baseline', str(baseline),
                    '--update-baseline')
    out = capsys.readouterr().out

    assert rc == 0, 'the run being updated must not also fail on the old floor'
    assert 'baseline robot_a: 59 -> 9' in out
    assert guard.load_baseline(baseline) == {
        'robot_a': 9, guard.TOOLING_PACKAGE: 3}


def test_update_baseline_drops_packages_that_no_longer_exist(
        workspace, tmp_path):
    """A whole-workspace update prunes; nothing else may edit the file."""
    source_dir, build_base = workspace
    write_source_package(source_dir, 'robot_a')
    write_result(build_base, 'robot_a', tests=5)
    write_result(build_base, guard.TOOLING_PACKAGE, tests=3)
    baseline = write_baseline_file(
        tmp_path / 'baseline.json',
        {'robot_a': 1, 'robot_gone': 40, guard.TOOLING_PACKAGE: 3})

    audit_only(source_dir, build_base, '--baseline', str(baseline),
               '--update-baseline')

    assert 'robot_gone' not in guard.load_baseline(baseline)


def test_a_narrowed_update_leaves_the_other_packages_alone(workspace,
                                                           tmp_path):
    """A partial run is not a whole-workspace verdict, so it prunes nothing."""
    source_dir, build_base = workspace
    write_source_package(source_dir, 'robot_a')
    write_source_package(source_dir, 'robot_b')
    write_result(build_base, 'robot_a', tests=5)
    baseline = write_baseline_file(tmp_path / 'baseline.json',
                                   {'robot_a': 1, 'robot_b': 40})

    audit_only(source_dir, build_base, '--baseline', str(baseline),
               '--update-baseline', '--packages-select', 'robot_a')

    assert guard.load_baseline(baseline) == {'robot_a': 5, 'robot_b': 40}


def test_update_baseline_refuses_to_bake_in_a_broken_run(workspace, capsys,
                                                         tmp_path):
    """A floor cut from a hollow run would enshrine the hollowness."""
    source_dir, build_base = workspace
    write_source_package(source_dir, 'robot_a')
    write_source_package(source_dir, 'robot_b')
    write_result(build_base, 'robot_a', tests=5)
    write_result(build_base, 'robot_b', tests=0)
    write_result(build_base, guard.TOOLING_PACKAGE, tests=3)
    baseline = write_baseline_file(tmp_path / 'baseline.json', {'robot_a': 1})

    rc = audit_only(source_dir, build_base, '--baseline', str(baseline),
                    '--update-baseline')
    out = capsys.readouterr().out

    assert rc == 1
    assert 'refusing to update' in out and 'robot_b (zero-tests)' in out
    assert guard.load_baseline(baseline) == {'robot_a': 1}, 'file untouched'


def test_update_baseline_refuses_when_code_has_no_real_tests(workspace,
                                                             capsys, tmp_path):
    """Untested implementation code is never something to baseline away."""
    source_dir, build_base = workspace
    package = write_source_package(source_dir, 'robot_a')
    write_implementation(package, 'clamp.py', 'def clamp(v):\n    return v\n')
    write_result(build_base, 'robot_a', tests=3, linters=3)
    write_result(build_base, guard.TOOLING_PACKAGE, tests=3)
    baseline = write_baseline_file(tmp_path / 'baseline.json', {'robot_a': 0})

    rc = audit_only(source_dir, build_base, '--baseline', str(baseline),
                    '--update-baseline')
    out = capsys.readouterr().out

    assert rc == 1
    assert 'robot_a (no-real-tests)' in out


def test_update_baseline_needs_a_baseline_for_a_foreign_source_tree(
        workspace):
    """Refuse to guess which file describes a workspace that is not ours."""
    source_dir, build_base = workspace
    write_source_package(source_dir, 'robot_a')
    write_result(build_base, 'robot_a', tests=5)

    with pytest.raises(SystemExit):
        audit_only(source_dir, build_base, '--update-baseline')
