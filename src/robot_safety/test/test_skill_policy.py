# Copyright (c) 2026 Jaime C.
#
# Use of this source code is governed by an MIT-style
# license that can be found in the LICENSE file or at
# https://opensource.org/licenses/MIT.

"""The tripwire: this layer's skill coverage is pinned to the shared registry.

The hazard these tests exist for is not a bug anyone can see today.  It is that
adding a skill upstream -- a routine feature -- would leave every other test in
this package green while the new skill flowed through unclamped, ungeometried
and outside the force check, because an ``isinstance`` chain defaults to
permissive.  So the vocabulary is enumerated once, and both the table and the
examples the rest of the suite runs on are checked against
``robot_skills.SKILL_TYPES``.

The runtime half of the same rule -- an unclassified skill is *refused*, not
waved through -- lives in ``test_safety_layer.py``.
"""

from dataclasses import fields, is_dataclass

import pytest
from robot_safety import (
    policy_for,
    SKILL_POLICIES,
    SkillPolicy,
    unclassified_skills,
    unrecognised_reason,
)
from robot_skills import Skill, SKILL_TYPES
from safety_fixtures import EXAMPLE_SKILLS, NameSquattingSkill, UnclassifiedSkill


def test_every_registered_skill_has_a_policy():
    """A skill added to the seam must be classified before this suite passes.

    If this fails, do not delete the assertion: decide whether the new skill
    closes jaws, carries a clampable scalar or names a point in space, and add
    the row to ``SKILL_POLICIES``.
    """
    assert unclassified_skills() == ()
    assert set(SKILL_POLICIES) == set(SKILL_TYPES)


def test_the_tripwire_actually_fires_on_an_unclassified_skill():
    """The check above is only reassuring if it can fail; prove that it can."""
    grown = {**SKILL_TYPES, 'wipe_surface': Skill}

    assert unclassified_skills(grown) == ('wipe_surface',)


def test_every_skill_the_suite_exercises_is_a_registered_one():
    """The parametrized skill lists are the registry, not a stale copy of it."""
    assert set(EXAMPLE_SKILLS) == set(SKILL_TYPES)
    for name, skill in EXAMPLE_SKILLS.items():
        assert isinstance(skill, SKILL_TYPES[name])


@pytest.mark.parametrize('name', sorted(SKILL_POLICIES))
def test_each_flag_implies_the_field_the_layer_will_reach_for(name):
    """A policy is a promise about the skill's shape; check it against the type.

    ``closes_jaws`` means the layer reads ``skill.side``, ``clamps_column_height``
    means it reads and rewrites ``skill.height``, ``has_cartesian_target`` means
    it reads ``skill.pose``.  Flagging a skill that has no such field would be
    an AttributeError at the worst possible moment.
    """
    skill_type = SKILL_TYPES[name]
    policy = SKILL_POLICIES[name]
    assert is_dataclass(skill_type)
    names = {field.name for field in fields(skill_type)}

    if policy.closes_jaws:
        assert 'side' in names, name
    if policy.clamps_column_height:
        assert 'height' in names, name
    if policy.has_cartesian_target:
        assert 'pose' in names, name


@pytest.mark.parametrize('name', sorted(SKILL_POLICIES))
def test_each_judgeable_field_implies_the_flag_that_judges_it(name):
    """The converse of the check above, and the one that closes the hole.

    "Flag implies field" catches a policy that promises more than the skill
    can deliver.  It does *not* catch the dangerous direction: a skill that
    carries a pose but was classified as a bare ``SkillPolicy()`` is invisible
    to the collision guard while every other test stays green -- the same
    permissive-by-default gap the policy table exists to close, reached by
    mis-classification instead of by omission.

    ``side`` is deliberately **not** checked in this direction, and the
    asymmetry is a decision, not an oversight: R11 says ``OpenGripper`` carries
    a ``side`` and must never be force-gated, because opening is the remedy for
    over-force.  A carried ``side`` therefore cannot imply ``closes_jaws``.
    """
    policy = SKILL_POLICIES[name]
    names = {field.name for field in fields(SKILL_TYPES[name])}

    if 'pose' in names:
        assert policy.has_cartesian_target, (
            f'{name} commands a pose that no collision guard would ever see')
    if 'height' in names:
        assert policy.clamps_column_height, (
            f'{name} carries a height that nothing would clamp')


def test_the_clamped_scalar_is_the_column_height_and_only_that():
    """Exactly one field is rewritten; anything else is a design change.

    Poses are aborted by the collision guard, never nudged: clamping is only
    sound for a scalar where "less of it" is strictly safer.
    """
    clamped = {name for name, policy in SKILL_POLICIES.items() if policy.clamps_column_height}

    assert clamped == {'extend_column'}


def test_opening_the_jaws_is_deliberately_unchecked():
    """R11 as a table row: the exemption is recorded, not implicit."""
    closing = {name for name, policy in SKILL_POLICIES.items() if policy.closes_jaws}

    assert closing == {'close_gripper', 'grasp'}
    assert not SKILL_POLICIES['open_gripper'].closes_jaws


def test_nothing_applies_and_nobody_decided_are_different_answers():
    """An empty policy is an explicit row; ``None`` means "unclassified"."""
    assert policy_for(EXAMPLE_SKILLS['navigate_to']) == SkillPolicy()
    assert policy_for(UnclassifiedSkill()) is None


def test_the_stand_in_skill_does_not_pollute_the_shared_registry():
    """The fixture stands for a gap; it must not become part of the vocabulary."""
    assert UnclassifiedSkill.name not in SKILL_TYPES
    assert isinstance(UnclassifiedSkill(), Skill)


def test_a_registered_name_on_the_wrong_type_gets_no_policy():
    """Lookup is by name, so the name has to be checked against the type.

    Otherwise a name-squatter is handed the real skill's policy and the layer
    reaches for a field it does not have -- an ``AttributeError`` from inside a
    gate whose whole contract is to *return* structured refusals.
    """
    squatter = NameSquattingSkill()

    assert squatter.name in SKILL_POLICIES
    assert policy_for(squatter) is None
    assert 'grasp' in unrecognised_reason(squatter)
    assert 'NameSquattingSkill' in unrecognised_reason(squatter)


def test_the_three_ways_of_being_unrecognisable_read_differently():
    """One verdict, three explanations: the log has to say which happened."""
    assert unrecognised_reason(EXAMPLE_SKILLS['grasp']) is None
    assert 'not a skill in the shared registry' in unrecognised_reason(UnclassifiedSkill())
    assert 'not the one that name promises' in unrecognised_reason(NameSquattingSkill())
