# Copyright (c) 2026 Jaime C.
#
# Use of this source code is governed by an MIT-style
# license that can be found in the LICENSE file or at
# https://opensource.org/licenses/MIT.

"""Tests for the driver half of ``scripts/check_test_integrity.py``.

The audit is only half the guard; the other half is the driver that
``pixi run test`` actually invokes. Its exit code is the thing that decides
whether a red workspace can merge, and it is load-bearing precisely because
``colcon test`` **exits 0 on a genuine test failure** (observed, see
``implementation.md`` §4): the only reason a real failure turns into a
non-zero ``pixi run test`` is that the driver folds every stage's return code
into its own. These tests replace the subprocesses with recorders so that
composition can be asserted directly, cheaply and hermetically.
"""

import check_test_integrity as guard
import pytest
from test_audit import write_result, write_source_package

#: The stage labels the driver prints in ``FAILED stages: ...``.
COLCON_TEST = 'colcon test'
TOOLING = 'workspace-tooling tests'
COLCON_TEST_RESULT = 'colcon test-result'
AUDIT = 'test integrity audit'
STAGES = (COLCON_TEST, TOOLING, COLCON_TEST_RESULT, AUDIT)


class FakeWorkspace:
    """A workspace whose colcon stages the test fully controls.

    ``guard._run``, ``guard.run_tooling_tests`` and ``guard.delete_result_files``
    are replaced with recorders; the fake ``colcon test`` writes the JUnit
    results a real one would, so the audit still runs against real files.
    """

    def __init__(self, tmp_path, monkeypatch):
        """Lay out ``src``/``build`` and patch the driver's subprocesses."""
        self.source_dir = tmp_path / 'src'
        self.build_base = tmp_path / 'build'
        self.source_dir.mkdir()
        self.build_base.mkdir()
        write_source_package(self.source_dir, 'robot_a')
        write_source_package(self.source_dir, 'robot_b')

        #: exit code each stage will report
        self.rc = {COLCON_TEST: 0, TOOLING: 0, COLCON_TEST_RESULT: 0}
        #: package -> collected tests the fake ``colcon test`` will record
        self.produces = {'robot_a': 5, 'robot_b': 7}
        self.tooling_produces = 3
        #: ordered log of what the driver did
        self.events = []
        self.commands = []

        self._delete = guard.delete_result_files
        monkeypatch.setattr(guard, '_run', self._fake_run)
        monkeypatch.setattr(guard, 'run_tooling_tests', self._fake_tooling)
        monkeypatch.setattr(
            guard, 'delete_result_files', self._fake_delete)

    def _fake_delete(self, build_base, packages=None):
        self.events.append('delete')
        self.deleted_packages = packages
        return self._delete(build_base, packages)

    def _fake_run(self, cmd, *, cwd=None):
        cmd = [str(c) for c in cmd]
        self.commands.append(cmd)
        stage = ' '.join(cmd[:2])
        self.events.append(stage)
        if stage == COLCON_TEST:
            selected = self.selection(cmd)
            for package, tests in self.produces.items():
                if selected is None or package in selected:
                    write_result(self.build_base, package, tests=tests)
        return self.rc[stage]

    def _fake_tooling(self, repo_root, build_base):
        self.events.append(TOOLING)
        if self.tooling_produces is not None:
            write_result(build_base, guard.TOOLING_PACKAGE,
                         tests=self.tooling_produces)
        return self.rc[TOOLING]

    @staticmethod
    def selection(cmd):
        """Return the ``--packages-select`` names in ``cmd``, or None."""
        if '--packages-select' not in cmd:
            return None
        return cmd[cmd.index('--packages-select') + 1:]

    def command(self, stage):
        """Return the recorded argv of ``stage`` (fails if it never ran)."""
        for cmd in self.commands:
            if ' '.join(cmd[:2]) == stage:
                return cmd
        raise AssertionError(f'{stage} never ran: {self.commands}')

    def main(self, *argv):
        """Invoke the driver against this workspace."""
        return guard.main(['--source-dir', str(self.source_dir),
                           '--build-base', str(self.build_base), *argv])


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    """Return a :class:`FakeWorkspace` with every stage passing."""
    return FakeWorkspace(tmp_path, monkeypatch)


def test_a_run_where_every_stage_passes_exits_zero(workspace, capsys):
    """The baseline: without it, "fails when it should" proves nothing."""
    rc = workspace.main()
    out = capsys.readouterr().out

    assert rc == 0
    assert 'All stages passed.' in out
    assert 'AUDIT PASSED' in out
    assert workspace.events == [
        'delete', COLCON_TEST, TOOLING, COLCON_TEST_RESULT]


@pytest.mark.parametrize('failing', STAGES)
def test_no_single_failing_stage_can_be_swallowed(workspace, capsys, failing):
    """Each stage alone must be able to fail the run -- none is decorative."""
    if failing == AUDIT:
        # The audit is the only stage that fails on evidence rather than on
        # an exit code: robot_b collects nothing while colcon reports success.
        workspace.produces['robot_b'] = 0
    else:
        workspace.rc[failing] = 1

    rc = workspace.main()
    out = capsys.readouterr().out

    assert rc == 1
    failed_line = [ln for ln in out.splitlines()
                   if ln.startswith('FAILED stages: ')]
    assert failed_line, out
    assert failed_line[0] == f'FAILED stages: {failing}'
    assert 'All stages passed.' not in out


def test_a_failure_colcon_test_reports_as_success_still_fails_the_run(
        workspace, capsys):
    """`colcon test` exits 0 on genuine test failures; test-result does not.

    This is the exact path a real broken test takes to a red
    ``pixi run test``, so it gets a test of its own rather than only a
    parametrised case.
    """
    workspace.rc[COLCON_TEST] = 0
    workspace.rc[COLCON_TEST_RESULT] = 1

    rc = workspace.main()

    assert rc == 1
    assert f'FAILED stages: {COLCON_TEST_RESULT}' in capsys.readouterr().out


def test_a_hollow_package_fails_even_when_every_command_succeeds(
        workspace, capsys):
    """The issue #16 scenario end to end: colcon green, workspace hollow."""
    workspace.produces['robot_b'] = 0

    rc = workspace.main()
    out = capsys.readouterr().out

    assert rc == 1
    assert 'FAIL robot_b' in out
    assert 'AUDIT FAILED' in out


def test_every_stage_runs_and_the_report_prints_after_an_early_failure(
        workspace, capsys):
    """A failed build must not hide the summary that explains the failure."""
    workspace.rc[COLCON_TEST] = 1

    rc = workspace.main()
    out = capsys.readouterr().out

    assert rc == 1
    assert workspace.events == [
        'delete', COLCON_TEST, TOOLING, COLCON_TEST_RESULT]
    assert '=== test integrity audit' in out
    assert 'robot_a' in out and 'robot_b' in out


def test_several_failing_stages_are_all_named(workspace, capsys):
    """The report must not stop at the first failure."""
    workspace.rc[COLCON_TEST] = 1
    workspace.rc[TOOLING] = 2
    workspace.rc[COLCON_TEST_RESULT] = 1
    workspace.produces['robot_a'] = 0

    rc = workspace.main()
    out = capsys.readouterr().out

    assert rc == 1
    for stage in STAGES:
        assert stage in out


def test_results_are_deleted_before_colcon_test_runs(workspace):
    """Deletion after the run would clean the evidence it must judge."""
    workspace.main()

    assert workspace.events.index('delete') < \
        workspace.events.index(COLCON_TEST)
    assert workspace.deleted_packages is None


def test_a_stale_result_cannot_stand_in_for_a_package_colcon_skipped(
        workspace, capsys):
    """The pre-run clean is what makes "no result" mean "was not tested"."""
    write_result(workspace.build_base, 'robot_b', tests=99)
    del workspace.produces['robot_b']  # colcon "skips" robot_b this run

    rc = workspace.main()
    out = capsys.readouterr().out

    assert rc == 1
    assert 'FAIL robot_b: no JUnit result file' in out
    assert '99' not in out


def test_the_tooling_suite_is_audited_like_a_package(workspace, capsys):
    """Emptying the guard's own suite must fail the run like any other."""
    workspace.tooling_produces = None

    rc = workspace.main()
    out = capsys.readouterr().out

    assert rc == 1
    assert f'FAIL {guard.TOOLING_PACKAGE}' in out


def test_colcon_is_pointed_at_the_directories_the_audit_reads(workspace):
    """Overridden paths must not leave colcon and the audit in different trees."""
    workspace.main()

    test_cmd = workspace.command(COLCON_TEST)
    assert '--build-base' in test_cmd
    assert test_cmd[test_cmd.index('--build-base') + 1] == \
        str(workspace.build_base)
    assert test_cmd[test_cmd.index('--base-paths') + 1] == \
        str(workspace.source_dir)
    result_cmd = workspace.command(COLCON_TEST_RESULT)
    assert result_cmd[result_cmd.index('--test-result-base') + 1] == \
        str(workspace.build_base)


def test_a_narrowed_run_is_passed_through_and_stays_honest(workspace, capsys):
    """Narrowing must keep the guard, skip the tooling suite, and say so."""
    rc = workspace.main('--packages-select', 'robot_a')
    out = capsys.readouterr().out

    assert rc == 0
    assert FakeWorkspace.selection(workspace.command(COLCON_TEST)) == \
        ['robot_a']
    assert TOOLING not in workspace.events
    assert guard.TOOLING_PACKAGE not in out
    assert 'PARTIAL RUN' in out
    assert 'robot_b' not in out


def test_a_narrowed_run_deletes_only_the_selected_results(workspace):
    """A narrow run must not destroy evidence it is not going to regenerate."""
    kept = write_result(workspace.build_base, 'robot_b', tests=7)

    workspace.main('--packages-select', 'robot_a')

    assert workspace.deleted_packages == ['robot_a']
    assert kept.exists()


def test_a_narrowed_run_still_fails_on_a_hollow_selected_package(workspace,
                                                                 capsys):
    """Narrowing is a smaller verdict, not a weaker one."""
    workspace.produces['robot_a'] = 0

    rc = workspace.main('--packages-select', 'robot_a')

    assert rc == 1
    assert 'FAIL robot_a' in capsys.readouterr().out


def test_a_bad_source_dir_fails_before_anything_is_run(workspace):
    """Nothing should run against a tree the guard could not even find."""
    with pytest.raises(SystemExit) as exc:
        workspace.main('--source-dir', str(workspace.source_dir / 'typo'))

    assert exc.value.code != 0
    assert workspace.events == []


def test_a_missing_command_is_an_error_not_a_traceback(capsys):
    """Running outside `pixi shell` is a common mistake; explain it."""
    rc = guard._run(['definitely-not-a-real-command-16'])
    out = capsys.readouterr().out

    assert rc != 0
    assert 'command not found' in out
