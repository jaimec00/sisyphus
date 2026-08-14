# Copyright (c) 2026 Jaime C.
#
# Use of this source code is governed by an MIT-style
# license that can be found in the LICENSE file or at
# https://opensource.org/licenses/MIT.

r"""Hold the run-completion marker match to the ONE line that really means done.

A dispatched run's tmux session lingers on purpose (the launcher's inner
command ends with ``exec bash``), so session-death cannot be the completion
signal. The signal is instead a marker the launcher writes into the run log as
a raw line at column 0::

    EXIT=0 (Fri Aug 14 07:18:03 AM EDT 2026)

Three places match that marker: ``scripts/pi/watch-run.sh`` (fires the
merge-eval wake, then reaps the session) and the ``run_finished`` guard in
``scripts/start-feature.sh`` / ``scripts/start-op.sh`` (reaps a leftover
session before re-dispatching a slug). All three used to match it with the
loose pattern ``EXIT=[0-9]+``, and that is not a completion marker -- it is a
string a *running* agent writes about itself. Claude Code bash steps routinely
``echo "EXIT=$?"``, and the run log is the agent's own stream-json transcript,
so those echoes land right back in the file being watched, embedded in a JSON
string (``"content":"EXIT=0\n..."``) and therefore never at column 0.

On 2026-08-14 the watcher matched one of those inline echoes, called a
mid-build op finished, fired a premature merge-eval wake and -- since PR #72
made the watcher reap consumed sessions -- killed the live run's tmux session.
``ops/op-pkgxml-validate`` died with its work uncommitted.

The fix is an anchor: ``^EXIT=[0-9]+ \(``. Only the launcher writes at column
0, so only the launcher can satisfy it. These tests extract the pattern each
script actually ships and run it against fixtures that reproduce both halves of
the bug, so the anchor cannot be dropped again without a red suite -- see
:func:`test_the_old_loose_pattern_would_fail_these_fixtures`, which pins the
fixtures to being a real reproduction rather than a self-fulfilling pair.
"""

from pathlib import Path
import re
import subprocess

import pytest

#: This file is ``<repo>/scripts/tests/test_run_completion_marker.py``.
REPO_ROOT = Path(__file__).resolve().parent.parent.parent

#: The Pi-side watcher: the one that wakes Sisyphus and reaps the session.
WATCHER = Path('scripts') / 'pi' / 'watch-run.sh'

#: The scripts that decide "this run finished" from the run log. Every one of
#: them acts on that verdict by killing a tmux session, so a false positive in
#: any of them destroys a live run.
COMPLETION_SCRIPTS = (
    WATCHER,
    Path('scripts') / 'start-feature.sh',
    Path('scripts') / 'start-op.sh',
)

#: The scripts that WRITE the marker (the tmux wrapper's last echo).
EMITTING_SCRIPTS = (
    Path('scripts') / 'start-feature.sh',
    Path('scripts') / 'start-op.sh',
)

#: Any ``grep -...E '<pattern>'`` whose pattern mentions ``EXIT=``. The scripts
#: quote these patterns in single quotes, including inside the double-quoted
#: string ``watch-run.sh`` hands to ``ssh``, so one regex finds them all.
GREP_PATTERN = re.compile(r"grep\s+-([a-zA-Z]*)E\s+'([^']*EXIT=[^']*)'")

#: The wrapper's emit, e.g. ``echo \"EXIT=\${PIPESTATUS[0]} (\$(date))\"``. It
#: lives inside the double-quoted ``inner`` string, hence the escaped quotes.
EMIT_PATTERN = re.compile(r'echo\s+\\"(EXIT=.*?)\\"')

#: The pattern that shipped before this file existed, and the whole reason it
#: does. Kept here as the negative control, not as anything's behavior.
LOOSE_PATTERN = 'EXIT=[0-9]+'


def write_log(directory, name, body):
    """Write one fixture run log and return its path."""
    path = Path(directory) / name
    path.write_text(body)
    return path


@pytest.fixture
def inline_echo_log(tmp_path):
    """Build a STILL-RUNNING agent's log, in which it echoed ``EXIT=$?``.

    This is the shape that killed ``ops/op-pkgxml-validate``: the text
    ``EXIT=0`` is present, but only ever inside a stream-json ``tool_result``
    string, so no line begins with it.
    """
    return write_log(tmp_path, 'inline_echo.log', (
        '{"type":"assistant","message":{"content":[{"type":"tool_use",'
        '"input":{"command":"pytest -q; echo \\"EXIT=$?\\""}}]}}\n'
        '{"type":"user","message":{"content":[{"type":"tool_result",'
        '"content":"5 passed\\nEXIT=0\\n"}]}}\n'
        '{"type":"assistant","message":{"content":[{"type":"text",'
        '"text":"Tests pass. Continuing with the implementation."}]}}\n'
    ))


@pytest.fixture
def inline_marker_lookalike_log(tmp_path):
    """Build a running agent's log that echoed the marker's FULL shape inline.

    An agent is free to print ``EXIT=0 (some date)`` -- matching the trailing
    ``` (``` alone is therefore not enough, and this fixture is what says so.
    Column 0 is the property no embedded string can forge.
    """
    return write_log(tmp_path, 'inline_lookalike.log', (
        '{"type":"user","message":{"content":[{"type":"tool_result",'
        '"content":"EXIT=0 (Fri Aug 14 07:18:03 AM EDT 2026)\\n"}]}}\n'
        '{"type":"assistant","message":{"content":[{"type":"text",'
        '"text":"Still working."}]}}\n'
    ))


@pytest.fixture
def real_marker_log(tmp_path):
    """Build a finished run's log: the raw marker line ends the transcript."""
    return write_log(tmp_path, 'real_marker.log', (
        '{"type":"user","message":{"content":[{"type":"tool_result",'
        '"content":"EXIT=1\\n"}]}}\n'
        '{"type":"result","subtype":"success","is_error":false}\n'
        'EXIT=0 (Fri Aug 14 07:18:03 AM EDT 2026)\n'
    ))


def read_script(relative_path):
    """Return the text of a script under the repository root."""
    return (REPO_ROOT / relative_path).read_text()


def exit_grep_patterns(relative_path):
    """Return the ``(flags, pattern)`` of every ``EXIT=`` grep in a script."""
    return GREP_PATTERN.findall(read_script(relative_path))


def grep(pattern, path, extra_flags=''):
    """Run the real ``grep -E`` and return ``(matched, stdout)``.

    The pattern comes straight out of the script under test and is handed to
    the same ``grep`` the script calls, so nothing here re-implements the
    matching that is the whole point.
    """
    result = subprocess.run(
        ['grep', '-' + extra_flags + 'E', pattern, str(path)],
        capture_output=True, text=True)
    assert result.returncode in (0, 1), result.stderr
    return result.returncode == 0, result.stdout


@pytest.mark.parametrize('script', COMPLETION_SCRIPTS, ids=str)
def test_every_completion_check_is_anchored(script):
    """Each script's marker greps must anchor at line start, not float."""
    patterns = exit_grep_patterns(script)
    assert patterns, f'{script}: no EXIT= grep found -- did the check move?'
    for _, pattern in patterns:
        assert pattern.startswith('^EXIT='), (
            f'{script}: unanchored marker pattern {pattern!r}; a running '
            "agent's own `echo EXIT=$?` would match it")


@pytest.mark.parametrize('script', COMPLETION_SCRIPTS, ids=str)
def test_a_running_agents_own_echo_is_not_completion(
        script, inline_echo_log, inline_marker_lookalike_log):
    """No shipped pattern may fire on a log the agent is still writing."""
    for flags, pattern in exit_grep_patterns(script):
        for log in (inline_echo_log, inline_marker_lookalike_log):
            matched, _ = grep(pattern, log)
            assert not matched, (
                f'{script}: pattern {pattern!r} (grep -{flags}E) reported '
                f'{log.name} complete -- this reaps a LIVE run')


@pytest.mark.parametrize('script', COMPLETION_SCRIPTS, ids=str)
def test_the_real_marker_is_completion(script, real_marker_log):
    """Every shipped pattern must still fire on a genuinely finished run."""
    for flags, pattern in exit_grep_patterns(script):
        matched, _ = grep(pattern, real_marker_log)
        assert matched, (
            f'{script}: pattern {pattern!r} (grep -{flags}E) missed the real '
            'marker -- the watcher would hang until the backstop cron')


def test_the_exit_info_read_returns_the_runs_own_code(real_marker_log):
    """``watch-run.sh``'s ``-o`` read must see the marker and nothing else.

    The inline echo in :func:`real_marker_log` is ``EXIT=1`` while the run's
    own marker is ``EXIT=0``. The loose read scrapes both and is rescued only
    by ``tail -1`` plus the accident that the marker is written last -- so
    assert the stronger property the anchor actually buys: exactly one
    candidate, the run's own, before any ``tail`` runs.
    """
    patterns = [pattern
                for flags, pattern in exit_grep_patterns(WATCHER)
                if 'o' in flags]
    assert patterns, 'watch-run.sh no longer reads the exit code with grep -o'
    for pattern in patterns:
        _, output = grep(pattern, real_marker_log, extra_flags='o')
        assert output.split() == ['EXIT=0'], (
            f'{pattern!r} read {output.split()!r} from the finished run; only '
            "the wrapper's own EXIT=0 marker is the run's exit code")


@pytest.mark.parametrize('script', EMITTING_SCRIPTS, ids=str)
def test_the_wrapper_emits_what_the_watcher_matches(script, tmp_path):
    """Run the launcher's own echo and match it with the watcher's pattern.

    Emitter and matcher are written in two different files, three directories
    apart, and only agree by convention -- so run the convention. The emit is
    executed by a real bash (after a pipeline, so ``PIPESTATUS`` is set) and the
    output is grepped with the pattern ``watch-run.sh`` actually ships.
    """
    emits = EMIT_PATTERN.findall(read_script(script))
    assert emits, f'{script}: the run wrapper no longer echoes an EXIT marker'

    watcher_patterns = [pattern
                        for flags, pattern in exit_grep_patterns(WATCHER)
                        if 'q' in flags]
    assert watcher_patterns, 'watch-run.sh no longer tests for completion'

    for emit in emits:
        # The emit is stored escaped for the double-quoted `inner` string.
        command = 'true | true\necho "%s"\n' % emit.replace('\\$', '$')
        result = subprocess.run(['bash', '-c', command],
                                capture_output=True, text=True)
        assert result.returncode == 0, result.stderr
        log = write_log(tmp_path, 'emitted.log', result.stdout)
        for pattern in watcher_patterns:
            matched, _ = grep(pattern, log)
            assert matched, (
                f'{script}: emits {result.stdout.strip()!r}, which '
                f'{pattern!r} does not match -- runs would never be seen '
                'as finished')


def test_the_old_loose_pattern_would_fail_these_fixtures(
        inline_echo_log, inline_marker_lookalike_log, real_marker_log):
    """Pin the fixtures to reproducing the bug, not merely passing today.

    Without this, a future rewrite could weaken the fixtures until the loose
    pattern passes them too and the anchor stops being load-bearing. The old
    pattern must match all three logs: that indiscriminacy IS the bug.
    """
    for log in (inline_echo_log, inline_marker_lookalike_log, real_marker_log):
        matched, _ = grep(LOOSE_PATTERN, log)
        assert matched, (
            f'{log.name} no longer reproduces the pre-fix false positive')


def test_no_other_script_matches_the_marker_loosely():
    """Sweep all of ``scripts/`` -- one straggler is one killable run."""
    offenders = []
    for path in sorted((REPO_ROOT / 'scripts').rglob('*.sh')):
        for flags, pattern in GREP_PATTERN.findall(path.read_text()):
            if not pattern.startswith('^EXIT='):
                offenders.append(
                    f'{path.relative_to(REPO_ROOT)}: grep -{flags}E '
                    f'{pattern!r}')
    assert not offenders, (
        'unanchored run-completion checks found:\n' + '\n'.join(offenders))
