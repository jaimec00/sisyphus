# Copyright (c) 2026 Jaime C.
#
# Use of this source code is governed by an MIT-style
# license that can be found in the LICENSE file or at
# https://opensource.org/licenses/MIT.

"""Tests for the skill command types: construction, validation, dispatch."""

from dataclasses import FrozenInstanceError

import pytest
from robot_skills import (
    CloseGripper,
    ExtendColumn,
    Grasp,
    MoveGripper,
    NavigateTo,
    OpenGripper,
    Place,
    Pose,
    Side,
    Skill,
    skill_from_dict,
    SKILL_TYPES,
)
from robot_skills.serialization import SerializationError

ALL_SKILLS = (
    NavigateTo('kitchen'),
    MoveGripper(Side.RIGHT, Pose.from_xyz(0.4, -0.2, 0.9)),
    Grasp('mug_1'),
    Grasp('mug_1', Side.RIGHT),
    Place(Pose.from_xyz(0.5, 0.1, 0.8)),
    Place(Pose.from_xyz(0.5, 0.1, 0.8), Side.LEFT),
    ExtendColumn(0.75),
    OpenGripper(Side.LEFT),
    CloseGripper(Side.RIGHT),
)


def test_every_documented_skill_is_registered():
    """The registry holds exactly the seven skills the brief specifies."""
    assert dict(SKILL_TYPES) == {
        'navigate_to': NavigateTo,
        'move_gripper': MoveGripper,
        'grasp': Grasp,
        'place': Place,
        'extend_column': ExtendColumn,
        'open_gripper': OpenGripper,
        'close_gripper': CloseGripper,
    }
    for wire_name, skill_type in SKILL_TYPES.items():
        assert skill_type.name == wire_name


def test_registry_is_read_only():
    """A caller cannot smuggle a new skill in behind the API's back."""
    with pytest.raises(TypeError):
        SKILL_TYPES['teleport'] = NavigateTo


def test_skills_are_frozen_data_objects():
    """Skills are inert data, so a safety layer can inspect them safely."""
    skill = ExtendColumn(0.5)
    with pytest.raises(FrozenInstanceError):
        skill.height = 2.0
    assert ExtendColumn(0.5) == skill
    assert ExtendColumn(0.6) != skill


def test_skill_base_class_is_abstract():
    """The base Skill is a contract, not something the brain can send."""
    with pytest.raises(TypeError):
        Skill()


@pytest.mark.parametrize('skill', ALL_SKILLS, ids=lambda s: repr(s))
def test_skill_round_trips_polymorphically(skill, round_trip):
    """Every skill survives to_dict/from_dict and JSON, via base or subclass."""
    round_trip(skill)
    assert skill_from_dict(skill.to_dict()) == skill
    assert type(skill).from_dict(skill.to_dict()) == skill
    assert skill.to_dict()['skill'] == skill.name


def test_side_accepts_its_wire_value():
    """A side may be given as the enum or as its string value."""
    assert MoveGripper('left', Pose()).side is Side.LEFT
    assert Grasp('mug_1', 'right').side is Side.RIGHT
    assert Grasp('mug_1').side is None
    with pytest.raises(ValueError):
        OpenGripper('middle')


def test_argument_validation():
    """Structurally invalid arguments are rejected at construction time."""
    with pytest.raises(ValueError):
        NavigateTo('   ')
    with pytest.raises(TypeError):
        NavigateTo(42)
    with pytest.raises(ValueError):
        Grasp('')
    with pytest.raises(TypeError):
        MoveGripper(Side.LEFT, (0.1, 0.2, 0.3))
    with pytest.raises(TypeError):
        Place('over there')
    with pytest.raises(TypeError):
        ExtendColumn('high')
    with pytest.raises(ValueError):
        ExtendColumn(float('nan'))


def test_extend_column_does_not_clamp():
    """Range policy belongs to the safety layer and the world model, not the type."""
    assert ExtendColumn(-3.0).height == -3.0
    assert ExtendColumn(99.0).height == 99.0


def test_parsing_rejects_unknown_or_mismatched_skills():
    """Dispatch failures are loud and name the offending skill."""
    with pytest.raises(SerializationError, match='unknown skill'):
        skill_from_dict({'skill': 'teleport', 'location': 'mars'})
    with pytest.raises(SerializationError, match='missing required key'):
        skill_from_dict({'location': 'kitchen'})
    with pytest.raises(SerializationError, match='got skill'):
        NavigateTo.from_dict({'skill': 'grasp', 'object_id': 'mug_1'})


def test_parsing_rejects_malformed_payloads():
    """Missing, unknown and mistyped arguments are all reported."""
    with pytest.raises(SerializationError, match='missing required key'):
        skill_from_dict({'skill': 'navigate_to'})
    with pytest.raises(SerializationError, match='unknown key'):
        skill_from_dict({'skill': 'navigate_to', 'location': 'kitchen', 'speed': 2})
    with pytest.raises(SerializationError, match='expected a number'):
        skill_from_dict({'skill': 'extend_column', 'height': 'high'})
    with pytest.raises(SerializationError, match='not a valid Side'):
        skill_from_dict({'skill': 'open_gripper', 'side': 'middle'})
    with pytest.raises(SerializationError, match='expected a mapping'):
        skill_from_dict({'skill': 'place', 'pose': 'on the table'})


def test_grasp_side_is_optional_on_the_wire():
    """An omitted or null side parses back to 'let the backend choose'."""
    assert skill_from_dict({'skill': 'grasp', 'object_id': 'mug_1'}) == Grasp('mug_1')
    assert skill_from_dict(
        {'skill': 'grasp', 'object_id': 'mug_1', 'side': None}) == Grasp('mug_1')
