# Copyright (c) 2026 Jaime C.
#
# Use of this source code is governed by an MIT-style
# license that can be found in the LICENSE file or at
# https://opensource.org/licenses/MIT.

"""The tool catalogue must not drift from the skill seam.

These tests check the *derivation*, not a transcript of today's schemas: the
tool set is compared to ``SKILL_TYPES``, property names to what
``Skill.to_dict`` writes, and each schema's ``required`` list to what
``skill_from_dict`` actually demands.  Adding or reshaping a skill therefore
either updates the tools automatically or fails here -- it cannot silently
leave an agent calling a tool whose schema lies.
"""

from dataclasses import dataclass
from typing import Any

from mcp_fixtures import connected, payload
import pytest
from robot_mcp import schema_for_type, skill_schema, tools
from robot_mcp.schemas import UnsupportedFieldType
from robot_skills import (
    Grasp,
    Point,
    Pose,
    SerializationError,
    Side,
    skill_from_dict,
    SKILL_TYPES,
)


def sample_for(schema: dict) -> Any:
    """Return a value satisfying ``schema``, filling in optional keys too.

    Numbers are 1.0 rather than 0.0 so a generated quaternion is not the
    all-zero one the seam rejects.
    """
    if 'enum' in schema:
        return schema['enum'][0]
    if schema['type'] == 'string':
        return 'sample_id'
    if schema['type'] == 'number':
        return 1.0
    if schema['type'] == 'object':
        return {name: sample_for(sub) for name, sub in schema['properties'].items()}
    raise AssertionError(f'no sample value for {schema}')


def object_schemas(schema: dict) -> list[dict]:
    """Return ``schema`` and every object schema nested anywhere inside it."""
    if schema.get('type') != 'object':
        return []
    found = [schema]
    for sub in schema['properties'].values():
        found += object_schemas(sub)
    return found


def test_the_tools_are_exactly_the_skills_plus_the_two_backend_calls():
    """A skill added to the seam becomes a tool; nothing else sneaks in."""
    assert tools.TOOL_NAMES == set(SKILL_TYPES) | set(tools.FIXED_TOOL_NAMES)
    assert [tool.name for tool in tools.TOOLS] == (
        sorted(SKILL_TYPES) + list(tools.FIXED_TOOL_NAMES))
    assert len({tool.name for tool in tools.TOOLS}) == len(tools.TOOLS)


def test_every_tool_is_described_and_takes_a_closed_object():
    """An agent needs prose to choose a tool and a schema to fill it in."""
    for tool in tools.TOOLS:
        assert tool.description and tool.description.strip(), tool.name
        for schema in object_schemas(tool.input_schema):
            assert schema['additionalProperties'] is False, tool.name
            assert set(schema) == {'type', 'properties', 'required', 'additionalProperties'}
            assert set(schema['required']) <= set(schema['properties']), tool.name


def test_a_skill_tool_is_described_by_the_skill_class_itself():
    """Descriptions come from the docstrings, so they cannot go stale."""
    grasp = next(tool for tool in tools.TOOLS if tool.name == 'grasp')
    assert Grasp.__doc__.strip().splitlines()[0] in grasp.description
    assert 'status' in grasp.description and 'observation' in grasp.description


@pytest.mark.parametrize('name', sorted(SKILL_TYPES))
def test_schema_properties_are_the_wire_names_to_dict_uses(name):
    """Criterion 1: tool arguments are the skill's own wire keys, exactly."""
    schema = skill_schema(SKILL_TYPES[name])
    arguments = sample_for(schema)

    skill = skill_from_dict({'skill': name, **arguments})

    assert set(schema['properties']) == set(skill.to_dict()) - {'skill'}


@pytest.mark.parametrize('name', sorted(SKILL_TYPES))
def test_required_properties_are_the_ones_the_seam_demands(name):
    """Every ``required`` list is checked against the real parser, key by key."""
    schema = skill_schema(SKILL_TYPES[name])
    arguments = sample_for(schema)
    assert schema['required'], f'{name}: no skill takes zero arguments'

    for omitted in schema['properties']:
        reduced = {key: value for key, value in arguments.items() if key != omitted}
        if omitted in schema['required']:
            with pytest.raises(SerializationError):
                skill_from_dict({'skill': name, **reduced})
        else:
            skill_from_dict({'skill': name, **reduced})


def test_the_nested_pose_schema_follows_the_wire_contract():
    """Pose is the one composite argument; its schema is checked the same way.

    Its Python fields all have defaults while its wire form requires
    ``position`` -- the schema must follow the wire, and this proves it does.
    """
    pose_schema = skill_schema(SKILL_TYPES['move_gripper'])['properties']['pose']
    arguments = sample_for(pose_schema)

    assert Pose.from_dict(arguments).to_dict() == arguments
    assert pose_schema['required'] == ['position']
    Pose.from_dict({key: value for key, value in arguments.items() if key != 'orientation'})
    with pytest.raises(SerializationError):
        Pose.from_dict({'orientation': arguments['orientation']})

    position = pose_schema['properties']['position']
    assert position['required'] == ['x', 'y', 'z']
    for omitted in position['properties']:
        partial = {key: value for key, value in arguments['position'].items() if key != omitted}
        with pytest.raises(SerializationError):
            Point.from_dict(partial)


def test_the_side_enum_is_read_off_the_enum():
    """The allowed values are the enum's, not a list copied into this package."""
    assert schema_for_type(Side) == {
        'type': 'string', 'enum': [side.value for side in Side]}
    assert schema_for_type(Side)['enum'] == [side.value for side in Side]


def test_an_optional_field_keeps_its_value_schema_and_leaves_required():
    """``Side | None`` describes a Side; optionality lives in ``required``."""
    assert schema_for_type(Side | None) == schema_for_type(Side)
    grasp = skill_schema(Grasp)
    assert 'side' in grasp['properties'] and 'side' not in grasp['required']


@pytest.mark.parametrize('annotation', [int, bool, list[str], str | int, 'Side'])
def test_an_unmapped_field_type_is_refused_loudly(annotation):
    """Guessing a schema is worse than failing: the mapper refuses to guess."""
    with pytest.raises(UnsupportedFieldType):
        schema_for_type(annotation)


@dataclass(frozen=True)
class _NewSkill:
    """Stand-in for a skill someone adds to the seam tomorrow."""

    target: str
    side: Side | None = None


@dataclass(frozen=True)
class _ExoticSkill:
    """Stand-in for a skill whose field type the mapper has never seen."""

    repeats: int


def test_a_new_skill_becomes_a_tool_without_touching_this_package(monkeypatch):
    """The catalogue is generated, so the seam is the only place to edit."""
    monkeypatch.setattr(
        tools, 'SKILL_TYPES', {**SKILL_TYPES, 'wipe_surface': _NewSkill})

    names = [tool.name for tool in tools.build_tools()]
    added = next(tool for tool in tools.build_tools() if tool.name == 'wipe_surface')

    assert names == sorted({*SKILL_TYPES, 'wipe_surface'}) + list(tools.FIXED_TOOL_NAMES)
    assert added.input_schema['required'] == ['target']
    assert added.input_schema['properties']['side'] == schema_for_type(Side)
    assert 'Stand-in for a skill' in added.description


def test_a_skill_the_mapper_cannot_describe_breaks_the_build(monkeypatch):
    """Criterion 4's teeth: no permissive schema is emitted as a fallback."""
    monkeypatch.setattr(
        tools, 'SKILL_TYPES', {**SKILL_TYPES, 'spin': _ExoticSkill})

    with pytest.raises(UnsupportedFieldType):
        tools.build_tools()


def test_a_skill_shadowing_a_fixed_tool_breaks_the_build(monkeypatch):
    """``reset`` and ``get_observation`` are not skills; a clash must be loud."""
    monkeypatch.setattr(tools, 'SKILL_TYPES', {**SKILL_TYPES, 'reset': Grasp})

    with pytest.raises(ValueError, match='reset'):
        tools.build_tools()


@pytest.mark.anyio
async def test_a_client_lists_the_same_catalogue(backend):
    """What a real MCP session sees over list_tools is the catalogue itself."""
    async with connected(backend) as client:
        listed = await client.list_tools()

        assert [tool.name for tool in listed.tools] == [tool.name for tool in tools.TOOLS]
        assert all(tool.input_schema['type'] == 'object' for tool in listed.tools)

        # ...and every listed skill tool is callable with a schema-valid payload.
        for tool in listed.tools:
            if tool.name in tools.FIXED_TOOL_NAMES:
                continue
            result = payload(await client.call_tool(tool.name, sample_for(tool.input_schema)))
            assert result['skill']['skill'] == tool.name
