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
Reading each expected value from the source that owns it, rather than typing it
here, is what this module aims at -- it is what makes the suite worth more than
the prompt it checks.  It is an aim, not an invariant: some assertions still
name a heading, a phrase or a code by hand, and ``SUPERSEDED_BODY_CLAIMS`` is
hand-typed because the drivetrain it describes has no live source in this
package at all -- see ``TestBodyDescription`` for why, and for what that costs.
"""

import re
from types import MappingProxyType
from typing import Mapping

from brain_fixtures import (
    example_calls,
    failure_table,
    inline_words,
    section,
    stated_numbers,
    tool_rows,
    tool_table,
    WITHHELD_TOOLS,
    without_fences,
)
import pytest
from robot_backends import default_world, MockBackend
from robot_brain import operating_prompt
from robot_mcp.tools import TOOL_NAMES, TOOLS
from robot_safety import SafetyLimits
from robot_skills import FailureCode, GripperState, NavigateTo, Side, SkillStatus

PROMPT = operating_prompt()

#: The tools this agent is actually given -- the served catalogue minus the
#: ones the shipped config withholds from it.  Teaching a tool the config
#: filters out would be worse than not teaching it: the agent would plan
#: around a call it never gets to make.
AGENT_TOOLS = frozenset(TOOL_NAMES) - frozenset(WITHHELD_TOOLS)

#: Body facts the decision log has *superseded*, mapping the descriptor that
#: replaced each to a pattern matching the claim it replaced.  D1 gave the
#: robot a four-wheel base; D26 traded it for the LeKiwi 3-omniwheel holonomic
#: base and D29 built that geometry in ``robot_description``, but the prompt
#: kept saying "four-wheel" for a day -- prose is retyped, never refactored, so
#: nothing went red.
#:
#: A pattern rather than a list of spellings, because the list came first and
#: had a hole: it caught "four-wheel" and "4-wheel" and missed "4 wheels",
#: which is the spelling D26 itself prints (``decisions.md``, "the '4 wheels'
#: aesthetic of D1") and therefore the one in front of anyone re-drafting that
#: sentence.  Matched case-insensitively.
#:
#: Add a row when a decision supersedes another body fact.  Nothing here will
#: remind you: see ``TestBodyDescription`` for what this ledger does not do.
SUPERSEDED_BODY_CLAIMS: Mapping[str, str] = MappingProxyType({
    '3-omniwheel holonomic': r'\b(?:four|4)[\s-]+wheel',
})

#: How the prompt states the arm's reach: ``0.85 m reach``.
_STATED_REACH = re.compile(r'(\d+(?:\.\d+)?) m reach')

#: How the prompt spells a small count in prose: "two arms with grippers".
#: Only the counts a body plausibly has -- a robot that grows a third arm
#: should fail here loudly rather than skip the check it can no longer spell.
COUNT_WORDS: Mapping[int, str] = MappingProxyType({1: 'one', 2: 'two', 3: 'three'})

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


def introduction() -> str:
    """Return the prompt's opening paragraphs -- where it describes the body.

    Everything above the first ``## `` heading, with fences dropped and
    whitespace collapsed.  Fences, because a phrase surviving in a fenced note
    is not the prompt introducing the robot.  Whitespace, because the prompt is
    hard-wrapped at ~80 columns and a claim that straddles a newline is still
    the claim: correcting the base re-wrapped this very sentence once already,
    and the column, arms, gripper and camera PRs each re-wrap it again.  A
    guard that goes red on a re-wrap of a correct sentence gets deleted, not
    fixed.
    """
    return ' '.join(without_fences(PROMPT).split('\n## ', 1)[0].split())


def live_vocabulary() -> set[str]:
    """Return every name the prompt is allowed to use in backticks.

    Assembled from the running system rather than listed here: the tool names,
    the wire keys of a real observation and a real result, every enum value on
    the wire, and the seed world's own ids.  A word in backticks that is *not*
    in this set is either a typo or an invention -- both of which teach the
    agent to call something that does not exist.  Withheld tools are *not* in
    it: naming one is as wrong as naming a fictional one, because the agent
    does not have it either.
    """
    backend = MockBackend()
    result = backend.execute(NavigateTo('kitchen'))
    world = default_world()
    values = {member.value for enum in (SkillStatus, FailureCode, Side, GripperState)
              for member in enum}
    return (
        set(AGENT_TOOLS)
        | {name for names, _ in SCHEMAS.values() for name in names}
        | wire_keys(result.to_dict())
        | values
        | {spec.object_id for spec in world.objects}
        | {spec.label for spec in world.objects}
        | set(world.locations)
        # JSON literals, which the prompt quotes when describing a payload.
        | {'null', 'true', 'false'}
    )


class TestBodyDescription:
    """The body the prompt opens by describing is the body this workspace has.

    Most of that opening sentence has a live source on this side of the skill
    API, and each part is read from its own: the arm count from ``Side`` and
    the Mock's one gripper per arm, the column's travel and the arm's reach
    from ``RobotModel``.  The **drivetrain** is the part with none on
    this side of the skill API -- it is described in ``robot_description``'s
    URDF, and this package does not depend on that.  Reading it means adding
    the edge, which is measured rather than assumed:
    ``get_package_share_directory('robot_description')`` raises under
    ``colcon test`` today, because this suite's dependencies are exactly the
    packages the brain meets *across* the seam D12/D13 put there to keep the
    hardware layer swappable.  Two honest halves to that: nothing enforces it
    (a test-time ROS import here is unguarded -- ``test_no_ros_runtime``
    covers what the *shipped* assets import, not what a test does), and the
    edge would buy one digit, since the URDF carries three wheel joints but
    the words "omniwheel" and "holonomic" only in a comment.  A design call,
    recorded as D30; the drivetrain gets a hand-typed ledger instead.

    Be clear about what that ledger pin buys, since it is the weakest thing
    here: it catches the claim we *already know* went stale, and it cannot
    notice the next one.  Named residue, so nobody has to rediscover it: the
    column check reads the word "extendable" rather than a number; the prompt
    does not mention the head camera at all today, so that fact will land
    ungated when it arrives; and nothing here notices a body claim this file
    has never heard of.
    """

    @pytest.mark.parametrize('current', sorted(SUPERSEDED_BODY_CLAIMS))
    def test_a_superseded_body_claim_is_not_still_taught(self, current):
        """The prompt described D1's four-wheel base for a day after D26 (#67).

        Matched over the whole prompt, not just the sentence that went wrong:
        a retired body fact has no business anywhere in it.
        """
        found = re.search(SUPERSEDED_BODY_CLAIMS[current], PROMPT, re.IGNORECASE)
        assert not found, (
            f'the prompt still claims "{found.group(0)}", which "{current}" superseded')

    @pytest.mark.parametrize('current', sorted(SUPERSEDED_BODY_CLAIMS))
    def test_the_descriptor_that_replaced_it_is_taught(self, current):
        """Deleting the stale claim is half the fix; the agent still needs the body.

        Scoped to the introduction, where the body is introduced, so the
        phrase has to be doing the describing: against the raw prompt this
        passes on a leftover mention in a fenced note sitting beside a
        sentence calling the robot anything at all.

        Note the asymmetry with the absence check above, which needs no such
        care: its pattern separates "four" from "wheel" with a whitespace-or-
        hyphen class, which eats a newline, so the wrap cannot hide a retired
        claim from it.  A plain substring is not wrap-tolerant, which is what
        ``introduction()`` normalises away.
        """
        assert current.lower() in introduction().lower(), (
            f'the prompt never introduces the robot as "{current}"')

    def test_the_matcher_catches_the_spellings_a_literal_list_did_not(self):
        """The ledger is a pattern now, and an untested pattern is a hole.

        The absence check can only ever observe a pattern *failing* to match,
        so a typo in one would leave it green forever.  These are the retypes
        the literal list it replaced let through, the ones it caught, and a
        claim that ends at punctuation.  They are a floor, not a proof: a
        narrower pattern satisfying all of them is constructible, so a change
        to the pattern wants reading, not just re-running.  A second row
        brings its own controls; spellings are specific to the claim retired.
        """
        pattern = SUPERSEDED_BODY_CLAIMS['3-omniwheel holonomic']
        for claim in (
            'a four-wheel base',
            'a 4-wheel base',
            'four wheels',
            '4 wheels',              # decisions.md's own spelling of D1's base
            'a 4 wheel base',
            'four  wheel base',      # a double space survives most retypes
            'FOUR-WHEEL BASE',
            'four-wheeled base',
            'The base is four-wheel.',   # a claim can end at punctuation
        ):
            assert re.search(pattern, claim, re.IGNORECASE), claim

    def test_no_matcher_fires_on_the_body_we_actually_have(self):
        """The other half: a guard that cries wolf gets deleted, not fixed.

        Every pattern in the ledger against prose the prompt is *entitled* to
        contain -- the sentence this issue installed, and the neighbouring
        numbers a wider pattern would swallow.
        """
        for pattern in SUPERSEDED_BODY_CLAIMS.values():
            for innocent in (
                'a 3-omniwheel holonomic base',
                'three omniwheels at 60, 180 and 300 degrees',
                'four objects are on the table',
                'Speed caps: base 0.6 m/s, column 0.15 m/s, arm 0.5 m/s.',
            ):
                assert not re.search(pattern, innocent, re.IGNORECASE), (pattern, innocent)

    def test_the_arms_and_their_grippers_are_the_ones_the_robot_serves(self):
        """The arms in the opening sentence are a body claim with a live source.

        The rest of the opening sentence was pinned before this one was: the
        count was checkable the whole time -- ``Side`` is the seam's own list
        of arms, imported here already, and the Mock emits exactly one gripper
        observation per member -- and the ledger above is for the drivetrain
        precisely because the drivetrain is the part that has *no* such
        source.  Leaving this unchecked while saying so was the gap.

        ``COUNT_WORDS`` turns the live number into the word the prose uses; a
        count it cannot spell is a ``KeyError`` here, which is the right kind
        of loud.  The gripper half is matched in the singular so that
        rewording "arms with grippers" to "each arm's gripper" stays green --
        the claim is that the arms have them, not the plural.
        """
        grippers = MockBackend().get_observation().robot.grippers
        assert {gripper.side for gripper in grippers} == set(Side)
        assert f'{COUNT_WORDS[len(Side)]} arms' in introduction()
        assert 'gripper' in introduction()

    def test_the_column_is_called_extendable_while_the_model_gives_it_travel(self):
        """Calling the column extendable is a claim about travel, and travel is live.

        Weaker than the reach check, and deliberately so: there the prompt and
        the model state the *same number*, while here the test supplies the
        step from "has travel" to the English word.  It is here because the
        alternative was leaving the clause entirely unread, and it catches the
        drift that matters -- a prompt calling the column fixed, or a model
        that stopped giving it anywhere to go.
        """
        model = default_world().robot
        assert model.max_column_height > model.min_column_height
        assert 'extendable' in introduction()

    def test_the_reach_the_examples_quote_is_the_live_one(self):
        """A worked example quotes the arm's reach, and nothing else checks it.

        ``TestSafetyEnvelope`` scopes to the safety section; this number is in
        a refusal message under "Worked examples", so retuning
        ``RobotModel.reach_radius`` used to move the prompt out from under
        every assertion in this file.  A set comparison in both directions, as
        there: a changed reach fails, and so does a second reach no model
        backs -- including the empty set, if the phrase is ever reworded away.

        The model's other distances are deliberately not asserted: the prompt
        never states the shoulder offsets, and matching ``0.5`` against "arm
        0.5 m/s" would be a coincidence, not a check.
        """
        model = default_world().robot
        stated = {float(number) for number in _STATED_REACH.findall(PROMPT)}
        assert stated == {model.reach_radius}, (
            f'the prompt quotes {sorted(stated)} as the reach; the model says '
            f'{model.reach_radius}')


class TestToolCatalogue:
    """Every tool the prompt teaches exists, and every tool that exists is taught."""

    def test_the_tool_table_lists_exactly_the_tools_the_agent_is_given(self):
        """A tool added to the catalogue, or withheld from the agent, fails here.

        Not ``TOOL_NAMES``: the shipped config filters ``WITHHELD_TOOLS`` out
        of this agent's allowlist, and a prompt that taught one of those would
        have the agent planning around a call it never gets to make.
        """
        assert set(tool_table(PROMPT)) == set(AGENT_TOOLS)

    def test_every_worked_example_calls_a_real_tool(self):
        """The examples are the part an agent imitates most literally."""
        called = {name for name, _ in example_calls(PROMPT)}
        assert called <= set(AGENT_TOOLS), called - set(AGENT_TOOLS)
        # ...and they are examples of *driving*, not a second catalogue.
        assert {'navigate_to', 'grasp', 'place'} <= called

    def test_a_withheld_tool_is_not_taught_at_all(self):
        """Silence, not a description: the agent cannot call what it lacks.

        The prompt used to describe ``reset`` as "a test/demo tool", which is
        an invitation dressed as documentation.  A tool the config filters out
        has no row, no example and no mention.
        """
        for name in WITHHELD_TOOLS:
            assert name not in tool_table(PROMPT)
            assert name not in {called for called, _ in example_calls(PROMPT)}
            assert f'`{name}`' not in PROMPT

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

    @pytest.mark.parametrize('name', sorted(AGENT_TOOLS))
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
        for name in sorted(AGENT_TOOLS):
            properties, required = SCHEMAS[name]
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
