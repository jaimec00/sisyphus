# Copyright (c) 2026 Jaime C.
#
# Use of this source code is governed by an MIT-style
# license that can be found in the LICENSE file or at
# https://opensource.org/licenses/MIT.

"""Tests for the ``package.xml`` validity check in the test-integrity guard.

A malformed manifest is the failure mode the gate could not see. ``colcon``
logs the parse error at DEBUG, reclassifies the package from ``ament_python``
to plain ``python``, and still calls the build successful; the package then
produces no test result, so the only visible symptom is a ``no-result`` audit
line naming neither the file nor the cause. These tests pin the three things
that close that: the malformed manifest is *found*, the failure *names the
file and the parse error*, and it happens *before anything else runs* -- a
misattributed symptom three stages later is what cost the ~40 minutes.

The recurring cause gets its own fixture: a literal ``--`` inside an XML
comment, which XML forbids and nothing else in the toolchain flags.
"""

from pathlib import Path

import check_test_integrity as guard
import pytest
from test_audit import write_source_package
from test_driver import COLCON_TEST, FakeWorkspace

REPO_ROOT = Path(__file__).resolve().parents[2]

#: The manifest that started this: valid but for the ``--`` in the comment.
DOUBLE_DASH_COMMENT = """<?xml version="1.0"?>
<package format="3">
  <!-- the base is holonomic -- three wheels, 120 degrees apart -->
  <name>robot_x</name>
</package>
"""

UNCLOSED_TAG = """<?xml version="1.0"?>
<package format="3">
  <name>robot_x</name>
</package
"""

NO_NAME = """<?xml version="1.0"?>
<package format="3">
  <description>a package that forgot to say what it is called</description>
</package>
"""


@pytest.fixture
def source_dir(tmp_path):
    """Return a source tree holding two well-formed packages."""
    directory = tmp_path / 'src'
    directory.mkdir()
    write_source_package(directory, 'robot_a')
    write_source_package(directory, 'robot_b')
    return directory


def break_manifest(source_dir, package, text=DOUBLE_DASH_COMMENT):
    """Overwrite one package's manifest and return its path."""
    manifest = Path(source_dir) / package / 'package.xml'
    manifest.write_text(text)
    return manifest


def test_a_well_formed_workspace_reports_no_problems(source_dir):
    """The baseline: without it, "reports a problem" proves nothing."""
    assert guard.validate_manifests(source_dir) == []


def test_this_repository_own_manifests_all_parse():
    """Guard the live tree, not just fixtures -- src/ must stay valid."""
    assert guard.validate_manifests(REPO_ROOT / 'src') == []


def test_a_double_dash_in_a_comment_is_caught(source_dir):
    """The exact break that slipped through: ``--`` inside an XML comment."""
    manifest = break_manifest(source_dir, 'robot_a')

    problems = guard.validate_manifests(source_dir)

    assert [path for path, _ in problems] == [manifest]
    assert 'not valid XML' in problems[0][1]


def test_the_problem_carries_the_parse_error_and_its_position(source_dir):
    """Naming the file is half of it; the parser's own reason is the rest."""
    break_manifest(source_dir, 'robot_a')

    problem = guard.validate_manifests(source_dir)[0][1]

    assert 'line 3' in problem, problem


def test_an_unclosed_tag_is_caught(source_dir):
    """Any parse failure, not just the one that prompted the check."""
    manifest = break_manifest(source_dir, 'robot_b', UNCLOSED_TAG)

    assert [path for path, _ in guard.validate_manifests(source_dir)] == [
        manifest]


def test_a_manifest_without_a_name_is_caught(source_dir):
    """Parseable is not the same as usable: colcon needs the <name>."""
    manifest = break_manifest(source_dir, 'robot_a', NO_NAME)

    assert guard.validate_manifests(source_dir) == [
        (manifest, 'has no <name> element')]


def test_every_bad_manifest_is_reported_not_just_the_first(source_dir):
    """One run, one fix list -- stopping at the first would serialise them."""
    first = break_manifest(source_dir, 'robot_a')
    second = break_manifest(source_dir, 'robot_b', NO_NAME)

    problems = guard.validate_manifests(source_dir)

    assert sorted(path for path, _ in problems) == sorted([first, second])


def test_find_manifests_names_the_file_and_the_reason(source_dir):
    """The single-manifest path raises rather than mislabelling the package."""
    manifest = break_manifest(source_dir, 'robot_a')

    with pytest.raises(ValueError) as caught:
        guard.find_manifests(source_dir)

    assert str(manifest) in str(caught.value)
    assert 'not valid XML' in str(caught.value)


def test_the_report_names_the_file_and_explains_the_cause(source_dir):
    """The banner has to be readable by whoever did not write this check."""
    manifest = break_manifest(source_dir, 'robot_a')

    report = guard.format_manifest_problems(
        guard.validate_manifests(source_dir))

    assert str(manifest) in report
    assert 'MANIFEST CHECK FAILED' in report
    assert 'DEBUG' in report


def test_the_driver_fails_before_running_anything(tmp_path, monkeypatch,
                                                  capsys):
    """The point of the check: it fires ahead of colcon, not after it.

    Letting the run proceed is what produced the original 40-minute hunt --
    colcon succeeds, the package silently produces no result, and the audit
    blames the missing result rather than the manifest.
    """
    workspace = FakeWorkspace(tmp_path, monkeypatch)
    manifest = break_manifest(workspace.source_dir, 'robot_a')

    rc = workspace.main()

    assert rc == 1
    assert workspace.events == []
    output = capsys.readouterr().out
    assert str(manifest) in output
    assert 'MANIFEST CHECK FAILED' in output
    assert 'AUDIT' not in output


def test_audit_only_fails_on_a_malformed_manifest_too(tmp_path, monkeypatch,
                                                      capsys):
    """A read-only re-read of old XML is no more trustworthy than a run."""
    workspace = FakeWorkspace(tmp_path, monkeypatch)
    break_manifest(workspace.source_dir, 'robot_a')

    rc = workspace.main('--audit-only')

    assert rc == 1
    assert 'MANIFEST CHECK FAILED' in capsys.readouterr().out


def test_a_repaired_manifest_lets_the_run_proceed(tmp_path, monkeypatch):
    """The check must be a gate, not a wall: fixing the file clears it."""
    workspace = FakeWorkspace(tmp_path, monkeypatch)
    break_manifest(workspace.source_dir, 'robot_a')
    assert workspace.main() == 1

    write_source_package(workspace.source_dir, 'robot_a')

    assert workspace.main() == 0
    assert COLCON_TEST in workspace.events
