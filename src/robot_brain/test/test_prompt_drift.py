# Copyright (c) 2026 Jaime C.
#
# Use of this source code is governed by an MIT-style
# license that can be found in the LICENSE file or at
# https://opensource.org/licenses/MIT.

"""The prompt is prose, so these tests are its type-checker.

Under D21 the prompt *is* the brain, and its failure mode is silence: rename a
skill, add an argument, retune a safety limit, and the prompt keeps reading
beautifully while teaching an agent to call something that no longer exists.
Nothing else in the workspace would notice.

So every checkable claim the prompt makes is compared here against the **live**
source that owns it -- ``robot_mcp``'s tool catalogue, each tool's own JSON
Schema, ``robot_safety``'s shipped limits and ``robot_backends``' seed world.
No expected value in this module is typed by hand.
"""

from brain_fixtures import (
    example_calls,
    failure_table,
    inline_words,
    section,
    stated_numbers,
    tool_rows,
    tool_table,
)
import pytest
from robot_backends import default_world, MockBackend
from robot_brain import operating_prompt
from robot_mcp.tools import TOOL_NAMES, TOOLS
from robot_safety import SafetyLimits
from robot_skills import FailureCode, GripperState, NavigateTo, Side, SkillStatus

PROMPT = operating_prompt()

#: Each tool's argument names and its required ones, from the shipped schemas.
SCHEMAS = {
    tool.name: (
        frozenset(tool.input_schema['properties']),
        frozenset(tool.input_schema['required']),
    )
    for tool in TOOLS
}


def wire_keys(value) -> set[str]:
    """Return every key appearing anywhere in a JSON structure, recursively."""
    keys: set[str] = set()
    if isinstance(value, dict):
        for key, nested in value.items():
            keys.add(key)
            keys |= wire_keys(nested)
    elif isinstance(value, list):
        for item in value:
            keys |= wire_keys(item)
    return keys


def live_vocabulary() -> set[str]:
    """Return every name the prompt is allowed to use in backticks.

    Assembled from the running system rather than listed here: the tool names,
    the wire keys of a real observation and a real result, every enum value on
    the wire, and the seed world's own ids.  A word in backticks that is *not*
    in this set is either a typo or an invention -- both of which teach the
    agent to call something that does not exist.
    """
    backend = MockBackend()
    result = backend.execute(NavigateTo('kitchen'))
    world = default_world()
    values = {member.value for enum in (SkillStatus, FailureCode, Side, GripperState)
              for member in enum}
    return (
        set(TOOL_NAMES)
        | {name for names, _ in SCHEMAS.values() for name in names}
        | wire_keys(result.to_dict())
        | values
        | {spec.object_id for spec in world.objects}
        | {spec.label for spec in world.objects}
        | set(world.locations)
        # JSON literals, which the prompt quotes when describing a payload.
        | {'null', 'true', 'false'}
    )


class TestToolCatalogue:
    """Every tool the prompt teaches exists, and every tool that exists is taught."""

    def test_the_tool_table_lists_exactly_the_served_tools(self):
        """A tool added to (or dropped from) the catalogue fails here."""
        assert set(tool_table(PROMPT)) == set(TOOL_NAMES)

    def test_every_worked_example_calls_a_real_tool(self):
        """The examples are the part an agent imitates most literally."""
        called = {name for name, _ in example_calls(PROMPT)}
        assert called <= set(TOOL_NAMES), called - set(TOOL_NAMES)
        # ...and they are examples of *driving*, not a second catalogue.
        assert {'navigate_to', 'grasp', 'place'} <= called

    def test_the_prompt_names_nothing_the_system_does_not_have(self):
        """No invented tool, field, code or object id anywhere in the prose.

        The check runs over inline code spans only: fenced examples show raw
        wire JSON, which is checked by parsing it instead (see the argument
        and worked-example tests).
        """
        unknown = inline_words(PROMPT) - live_vocabulary()
        assert not unknown, f'the prompt names {sorted(unknown)}, which do not exist'


class TestToolArguments:
    """What the prompt says each tool takes matches that tool's own schema."""

    @pytest.mark.parametrize('name', sorted(TOOL_NAMES))
    def test_the_table_teaches_exactly_the_schema_properties(self, name):
        """A renamed or added skill argument fails here, per tool."""
        properties, _ = SCHEMAS[name]
        assert set(tool_table(PROMPT)[name]) == set(properties)

    def test_the_table_says_which_arguments_are_optional(self):
        """Optionality is taught too, or the agent over- or under-specifies.

        Derived from the schemas: every tool with an optional argument must
        have the word "optional" in its row, and no other row may claim it.
        """
        rows = tool_rows(PROMPT)
        for name, (properties, required) in SCHEMAS.items():
            has_optional = bool(properties - required)
            assert ('optional' in rows[name]) is has_optional, name

    def test_every_example_call_would_deserialize_at_the_tool_boundary(self):
        """The examples' arguments are legal for the tool they call.

        ``example_calls`` already fails on JSON that will not parse; this adds
        the schema: no unknown key, and nothing required left out.
        """
        for name, arguments in example_calls(PROMPT):
            properties, required = SCHEMAS[name]
            assert set(arguments) <= set(properties), (name, arguments)
            assert set(required) <= set(arguments), (name, arguments)


class TestSafetyEnvelope:
    """Every number the prompt states as a limit comes from ``limits.yaml``."""

    def test_the_stated_envelope_is_exactly_the_shipped_limits(self):
        """Retuning a limit without retuning the prompt fails here.

        A set comparison in both directions: a changed limit fails, and so
        does a number the prompt states that no limit backs.
        """
        limits = SafetyLimits.defaults()
        expected = {
            limits.column.min_height,
            limits.column.max_height,
            limits.motion.max_gripper_force,
            *limits.motion.velocities.values(),
        }
        assert stated_numbers(section(PROMPT, 'The safety envelope')) == expected

    def test_the_prompt_teaches_what_a_clamp_and_an_abort_look_like(self):
        """Both verdicts are legible to the agent, or it cannot recover.

        A clamp is a *success* whose ``skill`` changed; an abort is a failure
        carrying the safety-owned code.  Confusing the two is the mistake this
        section exists to prevent.
        """
        envelope = section(PROMPT, 'The safety envelope')
        assert 'clamped' in envelope
        safety_code = next(code for code in FailureCode if code.is_safety_event)
        assert safety_code.value in envelope


class TestFailureCodes:
    """The recovery table covers the whole failure vocabulary, and nothing else."""

    def test_every_failure_code_has_a_documented_recovery(self):
        """A code added to the seam without a recovery leaves the agent stuck."""
        assert set(failure_table(PROMPT)) == {code.value for code in FailureCode}

    def test_the_safety_code_is_taught_as_unretryable(self):
        """``rejected`` is the one code where trying again is always wrong."""
        table = section(PROMPT, 'When a skill fails')
        safety_code = next(code for code in FailureCode if code.is_safety_event)
        assert f'`{safety_code.value}`' in table
        assert 'do not repeat' in table.lower()


class TestWorkedExamples:
    """The examples are runnable against the world the agent will actually meet."""

    def test_every_location_named_in_an_example_exists(self):
        """A worked example that drives somewhere fictional teaches a refusal."""
        world = default_world()
        locations = {
            arguments['location'] for name, arguments in example_calls(PROMPT)
            if name == 'navigate_to'
        }
        assert locations, 'no example navigates anywhere'
        assert locations <= set(world.locations), locations - set(world.locations)

    def test_every_object_named_in_an_example_exists_and_is_graspable(self):
        """Same for object ids -- and grasping the sofa would teach nonsense."""
        graspable = {
            spec.object_id for spec in default_world().objects if spec.graspable}
        grasped = {
            arguments['object_id'] for name, arguments in example_calls(PROMPT)
            if name == 'grasp'
        }
        assert grasped, 'no example grasps anything'
        assert grasped <= graspable, grasped - graspable

    def test_the_examples_cover_a_recovery_and_a_clamp(self):
        """PROJECT.md:29 asks for worked examples; these two are the load-bearing ones.

        One where a skill comes back failed and the agent recovers, one where
        safety changed what ran -- the two situations that turn a plausible
        plan into a wrong report.
        """
        examples = section(PROMPT, 'Worked examples')
        assert 'out_of_reach' in examples
        assert 'clamped' in examples
        # A recovery is a *repeated* call after a refusal, not an apology.
        assert examples.count('call place(') >= 3

    def test_the_place_pose_problem_is_taught(self):
        """``place`` needs metric coordinates, which an LLM must not invent.

        PROJECT.md:28 says no hand-typed coordinates for the brain; the skill
        still takes a pose, so the prompt has to teach deriving one from the
        observation.  (Giving ``place`` a semantic target is a design change,
        not a prompt fix -- deliberately out of scope here.)
        """
        guidance = section(PROMPT, 'Putting things down')
        assert 'pose' in guidance
        assert 'out_of_reach' in guidance
        # It must point at a pose the agent already has, not at a number.
        assert 'observation' in guidance

    def test_the_prompt_covers_what_the_architecture_requires(self):
        """PROJECT.md:29's contents list, present as sections."""
        for heading in (
            'How to work',
            'The tools',
            'What you get back',
            'When a skill fails',
            'The safety envelope',
            'Putting things down',
            'Worked examples',
        ):
            assert section(PROMPT, heading).strip()
