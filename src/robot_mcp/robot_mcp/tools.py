# Copyright (c) 2026 Jaime C.
#
# Use of this source code is governed by an MIT-style
# license that can be found in the LICENSE file or at
# https://opensource.org/licenses/MIT.

"""The MCP tool catalogue: one tool per skill, plus two perception tools.

The seven skill tools are generated from :data:`~robot_skills.SKILL_TYPES` --
name, description and argument schema all come from the skill class itself
(:mod:`robot_mcp.schemas`).  :data:`FIXED_TOOLS` is the small hand-written
addendum for the two backend calls that are *not* skills, ``get_observation``
and ``reset``.

The catalogue is built once, at import time, so a skill the mapper cannot
describe (or one that collides with a fixed tool name) fails the import rather
than shipping a broken tool to an agent.
"""

import inspect

import mcp_types as types
from robot_mcp.schemas import no_arguments_schema, skill_schema
from robot_skills import Skill, SKILL_TYPES

__all__ = [
    'build_tools',
    'FIXED_TOOL_NAMES',
    'OBSERVATION_TOOL',
    'RESET_TOOL',
    'TOOL_NAMES',
    'TOOLS',
]

#: Name of the tool returning the current scene without touching it.
OBSERVATION_TOOL = 'get_observation'

#: Name of the tool restoring the backend's seed world.
RESET_TOOL = 'reset'

#: The tools that are not skills, in listing order.
FIXED_TOOL_NAMES: tuple[str, ...] = (OBSERVATION_TOOL, RESET_TOOL)

_RESULT_NOTE = (
    'Returns the SkillResult dict (schema_version 1): "skill", "status" '
    '("ok" or "failed"), "code" and "reason" when the backend refuses, and '
    '"observation" -- the scene as it stands after the attempt. A refusal is '
    'a normal result, not an error: read "status" and "code".'
)

_FIXED_DESCRIPTIONS: dict[str, str] = {
    OBSERVATION_TOOL: (
        'Look at the scene without changing it.\n\n'
        'Returns the Observation dict (schema_version 1): "robot" (pose, '
        'named location, column height, per-gripper state), "objects" (id, '
        'label, pose, graspable, held_by) and "known_locations" -- the names '
        'navigate_to accepts.'
    ),
    RESET_TOOL: (
        'Restore the backend to its seed world, undoing everything done so '
        'far.\n\nReturns the Observation dict of the restored scene, in the '
        'same shape as get_observation.'
    ),
}


def _check_name_collisions(skill_names: tuple[str, ...], fixed_names: tuple[str, ...]) -> None:
    """Raise if a skill's wire name would shadow one of the fixed tools.

    The two are separate namespaces upstream (``SKILL_TYPES`` knows nothing of
    ``get_observation``), so only this module can notice the clash -- and it
    must, because one of the two tools would otherwise become unreachable.
    """
    collisions = sorted(set(skill_names) & set(fixed_names))
    if collisions:
        raise ValueError(
            f'skill name(s) {", ".join(collisions)} collide with the fixed '
            f'robot_mcp tools ({", ".join(fixed_names)}); rename one of them')


def _skill_description(skill_type: type[Skill]) -> str:
    """Return the tool description for a skill, taken from its own docstring."""
    summary = inspect.getdoc(skill_type)
    if not summary:
        raise ValueError(
            f'{skill_type.__name__} has no docstring to describe its tool with')
    return f'{summary}\n\n{_RESULT_NOTE}'


def build_tools() -> tuple[types.Tool, ...]:
    """Return the full tool catalogue: the skills, then the two fixed tools."""
    skill_names = tuple(sorted(SKILL_TYPES))
    _check_name_collisions(skill_names, FIXED_TOOL_NAMES)
    tools = [
        types.Tool(
            name=name,
            description=_skill_description(SKILL_TYPES[name]),
            inputSchema=skill_schema(SKILL_TYPES[name]),
        )
        for name in skill_names
    ]
    tools += [
        types.Tool(
            name=name,
            description=_FIXED_DESCRIPTIONS[name],
            inputSchema=no_arguments_schema(),
        )
        for name in FIXED_TOOL_NAMES
    ]
    return tuple(tools)


#: The catalogue every server instance serves, built once at import time.
TOOLS: tuple[types.Tool, ...] = build_tools()

#: Every name :func:`robot_mcp.server.build_server` will answer to.
TOOL_NAMES: frozenset[str] = frozenset(tool.name for tool in TOOLS)
