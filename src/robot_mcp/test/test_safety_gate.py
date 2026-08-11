# Copyright (c) 2026 Jaime C.
#
# Use of this source code is governed by an MIT-style
# license that can be found in the LICENSE file or at
# https://opensource.org/licenses/MIT.

"""The gate below the tool boundary: what safety clamps, aborts, and lets past.

The point of every test here is the *seam*, not the limits themselves --
``robot_safety`` owns those and tests them.  What must hold here is that a tool
call cannot reach a backend without a verdict, that the verdict is legible in
the ordinary ``SkillResult`` wire form (D18, no new field), and that the server
an operator gets by default -- ``build_server()`` with no arguments -- is the
gated one (invariant 3).
"""

from dataclasses import replace

from mcp_fixtures import connected, payload
import pytest
from robot_backends import MockBackend, MockWorld, RobotModel
from robot_mcp import build_server, default_safety_layer, SkillToolRouter
from robot_safety import (
    KeepOutBoxGuard,
    NullCollisionGuard,
    SafetyEvent,
    SafetyEventKind,
    SafetyLayer,
    SafetyLimits,
)
from robot_skills import ExtendColumn, Pose, SkillResult, SkillStatus

pytestmark = pytest.mark.anyio

#: The travel range the shipped limits allow, read from the file, never typed.
COLUMN = SafetyLimits.defaults().column

#: A gripper target well below the floor, inside the shipped keep-out region.
UNDERGROUND = Pose.from_xyz(0.30, 0.10, -0.50)

#: The keep-out regions the shipped limits configure, read from the file.
KEEP_OUT_BOXES = SafetyLimits.defaults().keep_out_boxes


class RecordingBackend(MockBackend):
    """A Mock that remembers every skill that actually reached ``execute``.

    An abort is only an abort if the backend never saw the command, and the
    only way to check that without trusting the layer is to ask the backend.
    """

    def __init__(self, world: MockWorld | None = None) -> None:
        """Start with an empty log, then behave exactly like a Mock."""
        self.executed = []
        super().__init__(world)

    def execute(self, skill):
        """Record the skill, then execute it normally."""
        self.executed.append(skill)
        return super().execute(skill)


class NotingBackend(MockBackend):
    """A Mock whose successful results carry an informational note of their own.

    Today's Mock happens to return no note from the one clamped skill
    (``extend_column``), so "backend note *and* clamp note" would otherwise be
    an unreachable branch.  A Sim or Real backend with something to say about a
    lift motion would reach it immediately.
    """

    NOTE = 'lift motor was already warm'

    def execute(self, skill):
        """Add a note to every success, leaving refusals alone."""
        result = super().execute(skill)
        return replace(result, reason=self.NOTE) if result.succeeded else result


class VetoGuard:
    """A collision guard that refuses everything, with a recognisable detail."""

    DETAIL = 'the cat is asleep in the workspace'

    def check(self, skill, state) -> SafetyEvent:
        """Abort every skill, whatever it is."""
        return SafetyEvent(kind=SafetyEventKind.COLLISION_RISK, detail=self.DETAIL)


def keep_out_layer() -> SafetyLayer:
    """Return a layer whose collision guard enforces the shipped keep-out boxes."""
    return SafetyLayer(collision_guard=KeepOutBoxGuard.from_limits(SafetyLimits.defaults()))


async def test_the_default_server_enforces_the_configured_keep_out_geometry():
    """No injection: ``build_server()``'s own gate refuses a sub-floor target.

    The whole point of the shipped ``keep_out_boxes`` -- "so the seam ships
    working, not merely declared" (``limits.yaml``) -- is that they are live
    where an LLM is on the other side.  A default server whose guard checked no
    geometry would make that entry dead configuration, and nothing else in the
    workspace would notice.
    """
    backend = RecordingBackend()

    async with connected(backend) as client:
        result = payload(await client.call_tool(
            'move_gripper', {'side': 'left', 'pose': UNDERGROUND.to_dict()}))

    assert result['status'] == 'failed'
    assert result['code'] == 'rejected'
    # Named, so an operator can find the region in limits.yaml and change it.
    assert 'below_floor' in result['reason'], result['reason']
    assert backend.executed == []


async def test_the_default_gate_reads_one_limit_set_for_envelope_and_geometry():
    """The clamp and the guard cannot disagree about which file they came from."""
    layer = default_safety_layer()

    assert isinstance(layer.collision_guard, KeepOutBoxGuard)
    assert layer.collision_guard.boxes == KEEP_OUT_BOXES
    assert layer.limits.column == SafetyLimits.defaults().column


def test_a_limits_file_with_no_regions_yields_a_null_guard_not_a_dead_server():
    """Configuring no keep-out region is a choice; it must not break startup.

    Answered before asking, so nothing is caught: a guard over no regions
    would veto nothing (``KeepOutBoxGuard.from_limits`` refuses to build one),
    and the honest answer is the null guard rather than no server at all.
    """
    boxless = SafetyLimits.from_yaml(
        'column: {min_height: 0.0, max_height: 1.2}\n'
        'velocity: {base: 0.6, column: 0.15, arm: 0.5}\n'
        'gripper: {max_force: 40.0}\n')

    layer = default_safety_layer(boxless)

    assert isinstance(layer.collision_guard, NullCollisionGuard)
    assert layer.limits is boxless


async def test_the_default_server_clamps_an_out_of_range_column_height(backend, reference):
    """No argument, no configuration: ``build_server()``'s own gate rewrites it.

    The same command run straight at the backend is *refused* (out of column
    travel), so this is not the backend being lenient -- the gate turned an
    impossible command into a legal one and said so, which is D17's whole
    point.
    """
    refused = reference.execute(ExtendColumn(9.0))
    assert refused.status is SkillStatus.FAILED
    assert refused.code.value == 'out_of_range'

    async with connected(backend) as client:
        result = payload(await client.call_tool('extend_column', {'height': 9.0}))

    assert result['status'] == 'ok'
    # What ran, not what was asked for.
    assert result['skill'] == {'skill': 'extend_column', 'height': COLUMN.max_height}
    assert result['observation']['robot']['column_height'] == COLUMN.max_height
    assert result['code'] is None
    assert '9' in result['reason'] and str(COLUMN.max_height) in result['reason']
    assert backend.get_observation().robot.column_height == COLUMN.max_height


async def test_a_below_range_height_is_clamped_up_to_the_stop(backend):
    """Clamping is two-sided: the low bound is a limit too, not a floor by luck."""
    async with connected(backend) as client:
        result = payload(await client.call_tool('extend_column', {'height': -3.0}))

    assert result['status'] == 'ok'
    assert result['skill']['height'] == COLUMN.min_height
    assert result['observation']['robot']['column_height'] == COLUMN.min_height


async def test_an_in_range_command_passes_through_byte_identically(backend, reference):
    """The gate is transparent on the happy path, which is most of the day.

    Dict-equal to the same skill run straight at a parallel backend: no note is
    invented, no field is touched, nothing is reworded.
    """
    async with connected(backend) as client:
        result = payload(await client.call_tool('extend_column', {'height': 0.6}))

    assert result == reference.execute(ExtendColumn(0.6)).to_dict()
    assert result['reason'] is None


async def test_a_clamp_keeps_the_backends_own_note_and_adds_its_own():
    """Two informational notes, both kept verbatim, backend's first."""
    backend = NotingBackend()

    async with connected(backend) as client:
        result = payload(await client.call_tool('extend_column', {'height': 9.0}))

    assert result['status'] == 'ok'
    assert result['reason'].startswith(f'{NotingBackend.NOTE}; ')
    assert 'clamped' in result['reason']
    assert str(COLUMN.max_height) in result['reason']


async def test_a_clamped_command_the_backend_still_refuses_keeps_both_stories():
    """A clamp is not a promise of success: the refusal survives it, code intact.

    This backend's column stops well below the configured safety limit, so the
    clamped height is still out of *its* range -- the case a Real backend with
    a shorter column would hit on day one.
    """
    world = MockWorld(
        locations={'dock': Pose.from_xyz(0.0, 0.0, 0.0)},
        start_location='dock',
        robot=RobotModel(max_column_height=0.5),
    )

    async with connected(MockBackend(world)) as client:
        result = payload(await client.call_tool('extend_column', {'height': 9.0}))

    assert result['status'] == 'failed'
    assert result['code'] == 'out_of_range'
    assert result['skill']['height'] == COLUMN.max_height
    # The backend explains why it still refused; safety explains the rewrite.
    assert 'outside the column range' in result['reason']
    assert 'clamped' in result['reason']


async def test_an_aborted_call_never_reaches_the_backend():
    """The gate is *before* the world, not a report about it.

    ``move_gripper`` at that pose would also be refused by the backend (it is
    far out of reach), so the assertion that matters is *which* layer answered:
    a ``rejected`` code, not ``out_of_reach``, and an empty execute log.
    """
    backend = RecordingBackend()

    async with connected(backend, keep_out_layer()) as client:
        before = payload(await client.call_tool('get_observation', {}))
        result = payload(await client.call_tool(
            'move_gripper', {'side': 'left', 'pose': UNDERGROUND.to_dict()}))
        after = payload(await client.call_tool('get_observation', {}))

    assert result['status'] == 'failed'
    assert result['code'] == 'rejected'
    assert backend.executed == []
    # Nothing ran, so the reported world is the pre-call world, unchanged.
    assert result['observation'] == before == after


async def test_an_abort_reports_the_safety_events_own_words(backend):
    """``robot_mcp`` relays safety vocabulary; it does not re-word it."""
    async with connected(backend, SafetyLayer(collision_guard=VetoGuard())) as client:
        result = payload(await client.call_tool(
            'place', {'pose': Pose.from_xyz(0.3, 0.1, 0.8).to_dict()}))

    assert result['status'] == 'failed'
    assert result['reason'] == VetoGuard.DETAIL
    assert result['code'] == 'rejected'


async def test_an_aborted_result_is_a_normal_result_the_agent_can_read(backend):
    """A refusal is data, not a protocol error, and it round-trips (D18)."""
    async with connected(backend, SafetyLayer(collision_guard=VetoGuard())) as client:
        raw = await client.call_tool('grasp', {'object_id': 'mug_1'})

    assert not raw.is_error
    result = payload(raw)
    assert result['schema_version'] == 1
    rebuilt = SkillResult.from_dict(result)
    assert rebuilt.code.is_safety_event
    assert not rebuilt.code.is_backend_refusal
    # The skill echoed back is the one the agent asked for: nothing ran, so
    # there is nothing rewritten to report.
    assert result['skill'] == {'skill': 'grasp', 'object_id': 'mug_1', 'side': None}


async def test_the_gate_is_per_server_and_cannot_be_switched_off(backend):
    """Every construction path ends up holding a real ``SafetyLayer``."""
    assert isinstance(SkillToolRouter(backend).safety, SafetyLayer)
    assert isinstance(SkillToolRouter(backend, None).safety, SafetyLayer)

    class PermissiveStub:
        """A duck-typed 'gate' that would wave everything through."""

        def filter(self, skill, state):  # noqa: A003
            """Accept anything, unchanged."""
            raise AssertionError('this must never be called')

    with pytest.raises(TypeError, match='must be a SafetyLayer'):
        SkillToolRouter(backend, PermissiveStub())
    with pytest.raises(TypeError, match='must be a SafetyLayer'):
        build_server(backend, PermissiveStub())


async def test_an_injected_layer_is_the_one_that_judges(backend):
    """Injection is real: a tighter limit set changes what the tools do."""
    tight = SafetyLimits.from_yaml(
        'column: {min_height: 0.0, max_height: 0.4}\n'
        'velocity: {base: 0.6, column: 0.15, arm: 0.5}\n'
        'gripper: {max_force: 40.0}\n')

    async with connected(backend, SafetyLayer(limits=tight)) as client:
        result = payload(await client.call_tool('extend_column', {'height': 1.0}))

    assert result['skill']['height'] == 0.4
    assert result['observation']['robot']['column_height'] == 0.4


async def test_an_injected_layer_is_used_as_given_not_added_to(backend):
    """Nothing is layered on top of a caller's own guard.

    A caller that injects a layer with no geometry check gets exactly that:
    the sub-floor target the *default* server refuses reaches the backend
    here, and is refused by the backend's own reachability rule instead. A
    gate that quietly added checks nobody asked for would be as surprising as
    one that dropped them.
    """
    async with connected(backend, SafetyLayer(collision_guard=NullCollisionGuard())) as client:
        result = payload(await client.call_tool(
            'move_gripper', {'side': 'left', 'pose': UNDERGROUND.to_dict()}))

    assert result['status'] == 'failed'
    assert result['code'] == 'out_of_reach'
