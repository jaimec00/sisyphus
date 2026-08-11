# Copyright (c) 2026 Jaime C.
#
# Use of this source code is governed by an MIT-style
# license that can be found in the LICENSE file or at
# https://opensource.org/licenses/MIT.

"""Readers that turn the operating prompt back into checkable structure.

The prompt is prose written for an LLM, and it stays that way -- generating it
from the catalogue would produce something no one wants to read and that
teaches nothing about *how* to drive the robot.  The price of hand-writing it
is drift, so these readers pull the checkable parts back out (the tool table,
the calls in the worked examples, the numbers in the safety section) and the
tests compare them with the live seam.

The prompt's own conventions are what make this possible, and they are worth
keeping when editing it:

* tools and wire fields are named in `backticks`;
* a tool table row is ``| `tool` | `arg`, `arg` | prose |``;
* a worked-example call is a line of the form ``call tool({"arg": value})``.

A plain module rather than ``conftest.py``, matching ``mcp_fixtures.py`` in
``robot_mcp``.
"""

import json
import re
from types import MappingProxyType
from typing import Any, Iterator, Mapping

#: Tools ``robot_mcp`` serves that the ``robot`` agent deliberately does *not*
#: get, each with the reason it is withheld.
#:
#: This is a classification, not an exclusion list to trim when a test goes
#: red: the suite asserts that the shipped config exposes exactly
#: ``TOOL_NAMES - WITHHELD_TOOLS`` and that the prompt teaches exactly the same
#: set, so a tool added to the seam fails both until somebody *decides* which
#: side of this mapping it belongs on.  Deciding is the point; the failure is
#: the prompt for the decision.
WITHHELD_TOOLS: Mapping[str, str] = MappingProxyType({
    'reset': (
        'restores the seed world, undoing everything: harmless against Mock, '
        'but this is the same tool boundary a Sim/Real backend will front '
        '(D9), where RobotBackend.reset() is real motion and real lost state. '
        'An autonomous planner that decides mid-chore to "start over" must not '
        'be able to. An operator who wants it drives the server directly.'
    ),
})

#: A fenced code block, which the inline-``code`` reader must not look inside:
#: the examples show raw wire JSON, which is not the prompt's own vocabulary.
_FENCE = re.compile(r'```.*?```', re.DOTALL)

#: An inline code span.
_INLINE = re.compile(r'`([^`\n]+)`')

#: One call in a worked example: ``call grasp({"object_id": "cup_1"})``.
_CALL = re.compile(r'^call (\w+)\((.*)\)\s*$', re.MULTILINE)

#: Any decimal number, for reading the safety section's stated limits.
_NUMBER = re.compile(r'\d+(?:\.\d+)?')

#: The identifier-ish pieces of an inline code span: ``grippers[].grasped``
#: contributes ``grippers`` and ``grasped``.
_WORD = re.compile(r'[A-Za-z_][A-Za-z0-9_]*')


def without_fences(text: str) -> str:
    """Return ``text`` with every fenced code block removed."""
    return _FENCE.sub('', text)


def inline_words(text: str) -> set[str]:
    """Return every identifier named in an inline code span, outside fences."""
    words: set[str] = set()
    for span in _INLINE.findall(without_fences(text)):
        words.update(_WORD.findall(span))
    return words


def section(text: str, heading: str) -> str:
    """Return the body of one ``## heading``, up to the next heading of any level.

    Raises rather than returning ``''`` for a missing heading: a test that
    silently passes on an empty section is worse than no test.
    """
    marker = f'\n## {heading}\n'
    start = text.find(marker)
    if start < 0:
        raise AssertionError(f'the prompt has no "## {heading}" section')
    body = text[start + len(marker):]
    following = re.search(r'^#{1,2} ', body, re.MULTILINE)
    return body if following is None else body[:following.start()]


def table_rows(text: str) -> Iterator[list[str]]:
    """Yield the cells of every Markdown table *body* row in ``text``.

    Body rows only: a row counts once the ``|---|`` separator has gone past,
    so a header cell that happens to be in backticks (the failure table's
    ``code`` column) is not mistaken for data.
    """
    in_body = False
    for line in text.splitlines():
        line = line.strip()
        if not line.startswith('|'):
            in_body = False
            continue
        if set(line) <= set('|-: '):
            in_body = True
            continue
        if in_body:
            yield [cell.strip() for cell in line.strip('|').split('|')]


def tool_table(text: str) -> dict[str, tuple[str, ...]]:
    """Return ``{tool name: argument names}`` as the prompt's tool table states it."""
    table: dict[str, tuple[str, ...]] = {}
    for cells in table_rows(section(text, 'The tools')):
        names = _INLINE.findall(cells[0])
        assert len(names) == 1, cells
        table[names[0]] = tuple(_INLINE.findall(cells[1]))
    return table


def tool_rows(text: str) -> dict[str, str]:
    """Return ``{tool name: the whole row}`` for the prompt's tool table.

    Lets a test read what a row *says* about its tool (that an argument is
    optional, say) without re-parsing the table.
    """
    rows = {}
    for cells in table_rows(section(text, 'The tools')):
        rows[_INLINE.findall(cells[0])[0]] = ' | '.join(cells)
    return rows


def failure_table(text: str) -> tuple[str, ...]:
    """Return the failure codes the prompt's recovery table documents."""
    return tuple(
        _INLINE.findall(cells[0])[0]
        for cells in table_rows(section(text, 'When a skill fails'))
    )


def example_calls(text: str) -> list[tuple[str, dict[str, Any]]]:
    """Return every ``call tool({...})`` in the worked examples, parsed.

    The arguments are parsed as JSON rather than eyeballed, so an example that
    would not deserialize at the tool boundary fails here first.
    """
    calls = []
    for name, arguments in _CALL.findall(text):
        parsed = json.loads(arguments) if arguments.strip() else {}
        assert isinstance(parsed, dict), (name, arguments)
        calls.append((name, parsed))
    return calls


def stated_numbers(text: str) -> set[float]:
    """Return every number stated in ``text``, as floats."""
    return {float(match) for match in _NUMBER.findall(text)}
