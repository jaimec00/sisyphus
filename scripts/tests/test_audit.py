# Copyright (c) 2026 Jaime C.
#
# Use of this source code is governed by an MIT-style
# license that can be found in the LICENSE file or at
# https://opensource.org/licenses/MIT.

"""Tests for the zero-test guard in ``scripts/check_test_integrity.py``.

The fixtures below are the XML shapes ``colcon test`` was **observed** to
write in this workspace (RoboStack Jazzy, colcon-core 0.21.1, pytest 8):
a ``<testsuites><testsuite .../></testsuites>`` document from pytest's
``--junit-xml``, and the bare ``<testsuite>`` placeholder colcon writes
before invoking pytest. No colcon run is needed to exercise the guard.
"""

import os
from pathlib import Path
import subprocess
import time

import check_test_integrity as guard
import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]

# The real result robot_backends produced, trimmed to one test case.
PYTEST_RESULT = """<?xml version="1.0" encoding="utf-8"?><testsuites \
name="pytest tests"><testsuite name="pytest" errors="{errors}" \
failures="{failures}" skipped="{skipped}" tests="{tests}" time="1.272" \
timestamp="2026-08-10T04:17:40.162289-04:00" hostname="olivia">{cases}\
</testsuite></testsuites>"""

CASE = '<testcase classname="pkg.test.test_thing" name="test_thing" \
time="0.001" />'

# What pytest writes for a test skipped by e.g. ``pytest.importorskip``.
SKIPPED_CASE = '<testcase classname="pkg.test.test_thing" name="test_thing" \
time="0.001"><skipped type="pytest.skip" message="could not import \
&#39;mujoco&#39;" /></testcase>'

# Verbatim from colcon_core/task/python/test/pytest.py: written *before*
# pytest runs so that an early crash still leaves a result file behind.
COLCON_PLACEHOLDER = """<?xml version="1.0" encoding="UTF-8"?>
<testsuite name="pkg" tests="1" failures="0" time="0" errors="1" skipped="0">
  <testcase classname="pkg" name="pytest.missing_result" time="0">
    <failure message="The test invocation failed without generating a result \
file."/>
  </testcase>
</testsuite>
"""


def write_result(build_base, package, *, tests, errors=0, failures=0,
                 skipped=0, name='pytest.xml'):
    """Write a pytest-shaped JUnit result for ``package`` and return its path."""
    directory = Path(build_base) / package
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / name
    path.write_text(PYTEST_RESULT.format(
        tests=tests, errors=errors, failures=failures, skipped=skipped,
        cases=SKIPPED_CASE * skipped + CASE * (tests - skipped)))
    return path


def write_source_package(source_dir, name):
    """Create a minimal package source tree and return its directory."""
    directory = Path(source_dir) / name
    directory.mkdir(parents=True, exist_ok=True)
    (directory / 'package.xml').write_text(
        f'<?xml version="1.0"?>\n<package format="3">\n'
        f'  <name>{name}</name>\n</package>\n')
    return directory


def git_init(directory):
    """Make ``directory`` a git work tree (no commit, the index is enough)."""
    subprocess.run(['git', 'init', '-q', str(directory)], check=True)


def git_track(repo, *paths):
    """Add ``paths`` to ``repo``'s index, which is what ``ls-files`` reads."""
    subprocess.run(['git', '-C', str(repo), 'add', '-f', *map(str, paths)],
                   check=True)


@pytest.fixture
def workspace(tmp_path):
    """Return an empty ``(source_dir, build_base)`` pair."""
    source_dir = tmp_path / 'src'
    build_base = tmp_path / 'build'
    source_dir.mkdir()
    build_base.mkdir()
    return source_dir, build_base


def test_a_result_with_collected_tests_passes(workspace):
    """A package reporting >0 tests is the only thing that counts as green."""
    _, build_base = workspace
    write_result(build_base, 'robot_x', tests=58)

    verdict = guard.audit_package('robot_x', build_base)

    assert verdict.ok
    assert verdict.status == 'ok'
    assert verdict.tests == 58


def test_zero_collected_tests_fails(workspace):
    """The headline case: colcon calls this green, the guard must not."""
    _, build_base = workspace
    write_result(build_base, 'robot_x', tests=0)

    verdict = guard.audit_package('robot_x', build_base)

    assert not verdict.ok
    assert verdict.status == 'zero-tests'
    assert '0 collected tests' in verdict.detail


def test_an_all_skipped_suite_fails(workspace):
    """A suite where no test body ran is the same hollow green as an empty one.

    ``pytest.importorskip`` on an unavailable dependency (or a blanket
    ``@pytest.mark.skip``) yields ``tests="12" skipped="12"``: colcon reports
    success and ``colcon test-result`` reports no errors.
    """
    _, build_base = workspace
    write_result(build_base, 'robot_x', tests=12, skipped=12)

    verdict = guard.audit_package('robot_x', build_base)

    assert not verdict.ok
    assert verdict.status == 'all-skipped'
    assert verdict.skipped == 12
    assert verdict.executed == 0
    assert 'all 12 were skipped' in verdict.detail


def test_one_test_that_actually_ran_is_enough(workspace):
    """Skips are only fatal when they account for *every* collected test."""
    _, build_base = workspace
    write_result(build_base, 'robot_x', tests=12, skipped=11)

    verdict = guard.audit_package('robot_x', build_base)

    assert verdict.ok
    assert (verdict.tests, verdict.skipped, verdict.executed) == (12, 11, 1)


def test_skips_are_summed_across_result_files(workspace):
    """A package is only all-skipped when nothing ran in *any* of its files."""
    _, build_base = workspace
    write_result(build_base, 'robot_x', tests=5, skipped=5, name='a.xml')
    write_result(build_base, 'robot_x', tests=2, skipped=2, name='b.xml')

    assert guard.audit_package('robot_x', build_base).status == 'all-skipped'

    write_result(build_base, 'robot_x', tests=1, skipped=0, name='c.xml')

    assert guard.audit_package('robot_x', build_base).ok


@pytest.mark.parametrize('attribute', ['skip', 'skipped', 'disabled'])
def test_every_skip_attribute_colcon_counts_is_counted(workspace, attribute):
    """Mirror colcon: ``skip``, ``skipped`` and ``disabled`` all mean skipped."""
    _, build_base = workspace
    directory = build_base / 'robot_x'
    directory.mkdir()
    (directory / 'pytest.xml').write_text(
        f'<?xml version="1.0"?><testsuite name="p" tests="4" failures="0" '
        f'{attribute}="4"/>')

    verdict = guard.audit_package('robot_x', build_base)

    assert verdict.skipped == 4
    assert verdict.status == 'all-skipped'


def test_the_skipped_count_is_visible_in_the_report(workspace):
    """A human reading the log must be able to see the skips, not just fail."""
    _, build_base = workspace
    write_result(build_base, 'robot_x', tests=12, skipped=12)

    report = guard.format_report(
        [guard.audit_package('robot_x', build_base)])

    assert 'skipped' in report
    assert '12 tests collected (12 skipped)' in report


def test_a_missing_result_file_is_as_fatal_as_zero_tests(workspace):
    """Silent absence is the same hollow green as an explicit ``tests="0"``."""
    _, build_base = workspace

    verdict = guard.audit_package('robot_never_ran', build_base)

    assert not verdict.ok
    assert verdict.status == 'no-result'


def test_a_build_dir_without_any_result_file_fails(workspace):
    """A build directory full of non-result XML is still no evidence."""
    _, build_base = workspace
    directory = build_base / 'robot_x'
    directory.mkdir()
    (directory / 'package.xml').write_text(
        '<?xml version="1.0"?><package format="3"><name>robot_x</name>'
        '</package>')

    verdict = guard.audit_package('robot_x', build_base)

    assert not verdict.ok
    assert verdict.status == 'no-result'


def test_colcons_placeholder_result_counts_as_no_result(workspace):
    """The ``pytest.missing_result`` stub records a run that never ran."""
    _, build_base = workspace
    directory = build_base / 'robot_x'
    directory.mkdir()
    (directory / 'pytest.xml').write_text(COLCON_PLACEHOLDER)

    verdict = guard.audit_package('robot_x', build_base)

    assert not verdict.ok
    assert verdict.status == 'no-result'
    assert 'pytest.missing_result' in verdict.detail


@pytest.mark.parametrize('body', [
    '<?xml version="1.0"?><testsuites name="x"><testsuite name="p"',
    '',
    '<?xml version="1.0"?><package format="3"><name>p</name></package>',
    '<?xml version="1.0"?><testsuite name="p" failures="0"/>',
    '<?xml version="1.0"?><testsuite name="p" tests="lots" failures="0"/>',
    '<?xml version="1.0"?><testsuite name="p" tests="-3" failures="0"/>',
], ids=['truncated', 'empty', 'not-a-result', 'no-tests-attr',
        'non-integer', 'negative'])
def test_malformed_results_are_not_mistaken_for_evidence(workspace, body):
    """Unparseable or non-xUnit XML must never be read as "tests ran"."""
    _, build_base = workspace
    directory = build_base / 'robot_x'
    directory.mkdir()
    (directory / 'pytest.xml').write_text(body)

    assert guard.parse_xunit(directory / 'pytest.xml') is None
    verdict = guard.audit_package('robot_x', build_base)
    assert not verdict.ok
    assert verdict.status == 'no-result'


def test_malformed_xml_does_not_hide_a_sibling_real_result(workspace):
    """A junk XML file next to a real result must not suppress the real one."""
    _, build_base = workspace
    write_result(build_base, 'robot_x', tests=4)
    (build_base / 'robot_x' / 'garbage.xml').write_text('<not-xunit/>')

    verdict = guard.audit_package('robot_x', build_base)

    assert verdict.ok
    assert verdict.tests == 4


def test_results_in_subdirectories_are_found(workspace):
    """Results are counted wherever under the package build dir they land."""
    _, build_base = workspace
    nested = build_base / 'robot_x' / 'test_results' / 'robot_x'
    nested.mkdir(parents=True)
    (nested / 'suite.xml').write_text(
        PYTEST_RESULT.format(
            tests=2, errors=0, failures=0, skipped=0, cases=CASE * 2))

    verdict = guard.audit_package('robot_x', build_base)

    assert verdict.ok
    assert verdict.tests == 2


def test_several_result_files_are_summed(workspace):
    """Multiple result files add up; one empty file does not veto the rest."""
    _, build_base = workspace
    write_result(build_base, 'robot_x', tests=3, name='pytest.xml')
    write_result(build_base, 'robot_x', tests=0, name='other.xml')

    verdict = guard.audit_package('robot_x', build_base)

    assert verdict.ok
    assert verdict.tests == 3


def test_a_stale_result_from_an_earlier_run_fails(workspace):
    """A leftover file must not make a package that was skipped look tested."""
    _, build_base = workspace
    path = write_result(build_base, 'robot_x', tests=58)
    long_ago = time.time() - 3600
    os.utime(path, (long_ago, long_ago))

    verdict = guard.audit_package('robot_x', build_base, min_mtime=time.time())

    assert not verdict.ok
    assert verdict.status == 'stale'


def test_a_result_written_by_this_run_is_accepted(workspace):
    """Freshness must not reject the results the current run just wrote."""
    _, build_base = workspace
    started = time.time()
    write_result(build_base, 'robot_x', tests=58)

    verdict = guard.audit_package('robot_x', build_base, min_mtime=started)

    assert verdict.ok


def test_a_stale_result_does_not_mask_a_fresh_empty_one(workspace):
    """Freshness is applied before counting, not after."""
    _, build_base = workspace
    stale = write_result(build_base, 'robot_x', tests=58, name='old.xml')
    long_ago = time.time() - 3600
    os.utime(stale, (long_ago, long_ago))
    started = time.time()
    write_result(build_base, 'robot_x', tests=0, name='pytest.xml')

    verdict = guard.audit_package('robot_x', build_base, min_mtime=started)

    assert not verdict.ok
    assert verdict.status == 'zero-tests'


def test_deleting_results_removes_only_result_files(workspace):
    """Cleaning before a run must not touch colcon's other XML files."""
    _, build_base = workspace
    result = write_result(build_base, 'robot_x', tests=1)
    manifest = build_base / 'robot_x' / 'package.xml'
    manifest.write_text(
        '<?xml version="1.0"?><package format="3"><name>robot_x</name>'
        '</package>')

    removed = guard.delete_result_files(build_base)

    assert removed == [result]
    assert not result.exists()
    assert manifest.exists()


def test_cleaning_can_be_limited_to_the_selected_packages(workspace):
    """A narrowed run must not destroy evidence it will not regenerate."""
    _, build_base = workspace
    selected = write_result(build_base, 'robot_a', tests=1)
    untouched = write_result(build_base, 'robot_b', tests=1)

    removed = guard.delete_result_files(build_base, ['robot_a'])

    assert removed == [selected]
    assert not selected.exists()
    assert untouched.exists()


def test_expected_packages_come_from_the_source_tree(workspace):
    """The expected set is the source tree, so a skipped package is caught."""
    source_dir, build_base = workspace
    write_source_package(source_dir, 'robot_a')
    write_source_package(source_dir, 'robot_b')
    write_result(build_base, 'robot_a', tests=5)

    packages = guard.find_source_packages(source_dir)
    verdicts = {v.name: v for v in guard.audit(packages, build_base)}

    assert packages == ['robot_a', 'robot_b']
    assert verdicts['robot_a'].ok
    assert not verdicts['robot_b'].ok
    assert verdicts['robot_b'].status == 'no-result'


def test_package_name_is_read_from_the_manifest_not_the_directory(workspace):
    """Colcon keys results by ``<name>``; so must the expected set."""
    source_dir, _ = workspace
    directory = source_dir / 'some_directory'
    directory.mkdir()
    (directory / 'package.xml').write_text(
        '<?xml version="1.0"?><package format="3"><name>robot_real</name>'
        '</package>')

    assert guard.find_source_packages(source_dir) == ['robot_real']


def test_a_manifest_without_a_name_is_an_error(workspace):
    """A manifest we cannot key on must abort rather than silently vanish."""
    source_dir, _ = workspace
    directory = source_dir / 'broken'
    directory.mkdir()
    (directory / 'package.xml').write_text(
        '<?xml version="1.0"?><package format="3"/>')

    with pytest.raises(ValueError):
        guard.find_source_packages(source_dir)


@pytest.fixture
def git_workspace(tmp_path):
    """Return a ``(source_dir, build_base)`` pair inside a real git repo."""
    git_init(tmp_path)
    source_dir = tmp_path / 'src'
    build_base = tmp_path / 'build'
    source_dir.mkdir()
    build_base.mkdir()
    return source_dir, build_base


def test_untracked_vendored_packages_are_not_expected(git_workspace):
    """``vcs import`` drops upstream sources under src/; we cannot test those.

    Holding them to the guard would make ``pixi run test`` permanently red
    (`robot.repos` is already planned in pixi.toml), and an unfixably red
    driver is one people route around.
    """
    source_dir, _ = git_workspace
    write_source_package(source_dir, 'robot_a')
    write_source_package(source_dir, 'mujoco_ros2_control')
    git_track(source_dir, 'robot_a/package.xml')

    expected, unowned = guard.discover_packages(source_dir)

    assert expected == ['robot_a']
    assert unowned == ['mujoco_ros2_control']


def test_an_untracked_package_is_reported_not_silently_dropped(git_workspace,
                                                               capsys):
    """Dropping a package out of the audit must be visible in the report."""
    source_dir, build_base = git_workspace
    write_source_package(source_dir, 'robot_a')
    write_source_package(source_dir, 'mujoco_ros2_control')
    git_track(source_dir, 'robot_a/package.xml')
    write_result(build_base, 'robot_a', tests=1)
    write_result(build_base, guard.TOOLING_PACKAGE, tests=3)

    rc = guard.main(['--audit-only', '--source-dir', str(source_dir),
                     '--build-base', str(build_base)])
    out = capsys.readouterr().out

    assert rc == 0
    assert 'mujoco_ros2_control is in the source tree but not tracked' in out


def test_a_tracked_package_that_stops_being_tested_still_fails(git_workspace):
    """The ownership filter must not weaken the guard for our own packages."""
    source_dir, build_base = git_workspace
    write_source_package(source_dir, 'robot_a')
    write_source_package(source_dir, 'robot_b')
    git_track(source_dir, 'robot_a/package.xml', 'robot_b/package.xml')
    write_result(build_base, 'robot_a', tests=5)

    verdicts = {v.name: v for v in guard.audit(
        guard.find_source_packages(source_dir), build_base)}

    assert verdicts['robot_a'].ok
    assert verdicts['robot_b'].status == 'no-result'


def test_a_first_party_package_cannot_opt_out_with_a_marker_file(
        git_workspace):
    """Ownership is decided by git, so no in-package file grants an exemption."""
    source_dir, _ = git_workspace
    directory = write_source_package(source_dir, 'robot_a')
    git_track(source_dir, 'robot_a/package.xml')
    (directory / 'COLCON_IGNORE').touch()
    (directory / 'AMENT_IGNORE').touch()
    (directory / '.test-integrity-exempt').touch()

    assert guard.find_source_packages(source_dir) == ['robot_a']


def test_a_gitignored_first_party_package_is_still_expected(git_workspace):
    """Being ignored is not enough; only leaving the index removes a package."""
    source_dir, _ = git_workspace
    write_source_package(source_dir, 'robot_a')
    git_track(source_dir, 'robot_a/package.xml')
    (source_dir.parent / '.gitignore').write_text('src/robot_a/\n')

    assert guard.find_source_packages(source_dir) == ['robot_a']


def test_outside_a_git_work_tree_every_package_is_expected(workspace):
    """Without git there is no ownership signal, so nothing is exempted."""
    source_dir, _ = workspace
    write_source_package(source_dir, 'robot_a')
    write_source_package(source_dir, 'robot_b')

    expected, unowned = guard.discover_packages(source_dir)

    assert expected == ['robot_a', 'robot_b']
    assert unowned == []


def test_the_real_workspace_manifests_are_tracked():
    """This repo's own packages must be seen as owned, not as vendored.

    Deliberately says nothing about *other* directories under ``src/``: a
    vcs-imported dependency (or a package someone has not ``git add``-ed
    yet) must not turn this suite -- and therefore ``pixi run test`` -- red.
    The report's "not tracked" note is the signal for those.
    """
    expected, unowned = guard.discover_packages(REPO_ROOT / 'src')

    ours = {'robot_backends', 'robot_brain', 'robot_bringup',
            'robot_description', 'robot_perception', 'robot_safety',
            'robot_skills'}
    assert ours <= set(expected)
    assert not ours & set(unowned)


def test_an_empty_source_dir_is_an_error(workspace, capsys):
    """Auditing nothing must never print AUDIT PASSED (a wrong --source-dir)."""
    source_dir, build_base = workspace

    with pytest.raises(SystemExit) as exc:
        guard.main(['--audit-only', '--source-dir', str(source_dir),
                    '--build-base', str(build_base)])

    assert exc.value.code != 0
    captured = capsys.readouterr()
    assert 'AUDIT PASSED' not in captured.out
    assert 'found no packages' in captured.err


def test_a_nonexistent_source_dir_is_an_error(workspace, capsys):
    """A mistyped path must fail loudly rather than audit an empty walk."""
    source_dir, build_base = workspace

    with pytest.raises(SystemExit) as exc:
        guard.main(['--audit-only', '--source-dir', str(source_dir / 'nope'),
                    '--build-base', str(build_base)])

    assert exc.value.code != 0
    assert 'AUDIT PASSED' not in capsys.readouterr().out


def test_a_source_tree_of_only_vendored_packages_is_an_error(git_workspace,
                                                             capsys):
    """Every package being unowned is indistinguishable from finding none."""
    source_dir, build_base = git_workspace
    write_source_package(source_dir, 'robot_a')
    write_source_package(source_dir, 'mujoco_ros2_control')
    git_track(source_dir, 'robot_a/package.xml')
    subprocess.run(['git', '-C', str(source_dir), 'rm', '-q', '--cached',
                    'robot_a/package.xml'], check=True)

    with pytest.raises(SystemExit):
        guard.main(['--audit-only', '--source-dir', str(source_dir),
                    '--build-base', str(build_base)])

    assert 'AUDIT PASSED' not in capsys.readouterr().out


def test_unexpected_build_dirs_are_reported_but_not_fatal(workspace):
    """Leftover build dirs are information, not a reason to fail."""
    source_dir, build_base = workspace
    write_source_package(source_dir, 'robot_a')
    write_result(build_base, 'robot_a', tests=1)
    write_result(build_base, 'robot_deleted', tests=1)

    packages = guard.find_source_packages(source_dir)

    assert guard.unexpected_result_dirs(packages, build_base) == \
        ['robot_deleted']
    assert all(v.ok for v in guard.audit(packages, build_base))


def test_the_report_shows_real_numbers_when_passing(workspace, capsys):
    """A human reading the log sees counts, not silence, on success."""
    source_dir, build_base = workspace
    write_source_package(source_dir, 'robot_a')
    write_result(build_base, 'robot_a', tests=58)
    write_result(build_base, guard.TOOLING_PACKAGE, tests=3)

    rc = guard.main(['--audit-only', '--source-dir', str(source_dir),
                     '--build-base', str(build_base)])
    out = capsys.readouterr().out

    assert rc == 0
    assert 'AUDIT PASSED' in out
    assert 'robot_a' in out and '58' in out
    assert '2 packages, 61 tests collected' in out


def test_the_cli_fails_and_names_the_empty_package(workspace, capsys):
    """The whole point: a non-zero exit plus a legible reason."""
    source_dir, build_base = workspace
    write_source_package(source_dir, 'robot_a')
    write_source_package(source_dir, 'robot_b')
    write_result(build_base, 'robot_a', tests=58)
    write_result(build_base, 'robot_b', tests=0)
    write_result(build_base, guard.TOOLING_PACKAGE, tests=3)

    rc = guard.main(['--audit-only', '--source-dir', str(source_dir),
                     '--build-base', str(build_base)])
    out = capsys.readouterr().out

    assert rc == 1
    assert 'AUDIT FAILED' in out
    assert 'FAIL robot_b' in out
    assert 'FAIL robot_a' not in out


def test_narrowing_to_an_unknown_package_is_rejected(workspace):
    """A typo in --packages-select must not quietly shrink the expected set."""
    source_dir, build_base = workspace
    write_source_package(source_dir, 'robot_a')

    with pytest.raises(SystemExit):
        guard.main(['--audit-only', '--source-dir', str(source_dir),
                    '--build-base', str(build_base),
                    '--packages-select', 'robot_typo'])


def test_the_real_workspace_packages_are_all_expected():
    """The guard must expect every package this repo actually ships."""
    packages = guard.find_source_packages(REPO_ROOT / 'src')

    assert set(packages) >= {
        'robot_backends', 'robot_brain', 'robot_bringup', 'robot_description',
        'robot_perception', 'robot_safety', 'robot_skills'}


def test_the_guards_own_suite_is_audited_too(workspace, capsys):
    """A full run expects the tooling pseudo-package, so it cannot be dropped."""
    source_dir, build_base = workspace
    write_source_package(source_dir, 'robot_a')
    write_result(build_base, 'robot_a', tests=1)

    rc = guard.main(['--audit-only', '--source-dir', str(source_dir),
                     '--build-base', str(build_base)])
    out = capsys.readouterr().out

    assert rc == 1, 'the tooling suite must be part of the expected set'
    assert f'FAIL {guard.TOOLING_PACKAGE}' in out
