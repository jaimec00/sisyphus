# Copyright (c) 2026 Jaime C.
#
# Use of this source code is governed by an MIT-style
# license that can be found in the LICENSE file or at
# https://opensource.org/licenses/MIT.

"""The collision-guard seam: a hook that actually holds something.

Real geometry is a later feature, but a hook whose only implementation returns
``None`` is a dead parameter rather than an extension point.  So two things are
tested here: that the injected protocol is honoured by the layer, and that the
shipped stub genuinely stops a command.
"""

import pytest
from robot_safety import (
    ClampedCall,
    CollisionGuard,
    KeepOutBox,
    KeepOutBoxGuard,
    NullCollisionGuard,
    SafetyEvent,
    SafetyEventKind,
    SafetyLayer,
    SafetyLimits,
    target_pose,
)
from robot_skills import (
    CloseGripper,
    ExtendColumn,
    Grasp,
    MoveGripper,
    NavigateTo,
    Place,
    Point,
    Pose,
    Side,
)
from safety_fixtures import make_limits, make_state

STOVE = KeepOutBox('stove_top', x_min=1.0, x_max=1.5, y_min=0.0, y_max=0.5, z_min=0.8)
BELOW_FLOOR = KeepOutBox('below_floor', z_max=-0.02)


def test_both_shipped_guards_satisfy_the_protocol():
    """Structural conformance, so an outside checker needs no inheritance."""
    assert isinstance(NullCollisionGuard(), CollisionGuard)
    assert isinstance(KeepOutBoxGuard(), CollisionGuard)
    assert not isinstance(object(), CollisionGuard)


def test_the_default_layer_checks_no_geometry():
    """The honest default: no robot model yet, so nothing claims to check one."""
    layer = SafetyLayer(limits=make_limits())

    assert isinstance(layer.collision_guard, NullCollisionGuard)
    assert layer.collision_guard.check(NavigateTo('kitchen'), make_state()) is None


@pytest.mark.parametrize(
    'point, inside',
    [
        pytest.param(Point(1.2, 0.2, 1.0), True, id='centre'),
        pytest.param(Point(1.0, 0.0, 0.8), True, id='on-the-lower-corner'),
        pytest.param(Point(1.2, 0.2, 40.0), True, id='unbounded-above'),
        pytest.param(Point(1.2, 0.2, 0.79), False, id='below-it'),
        pytest.param(Point(0.9, 0.2, 1.0), False, id='beside-it-in-x'),
        pytest.param(Point(1.2, 0.6, 1.0), False, id='beside-it-in-y'),
    ],
)
def test_a_keep_out_box_bounds_the_region_it_claims_to(point, inside):
    """An omitted bound is unbounded; a present bound is inclusive."""
    assert STOVE.contains(point) is inside


@pytest.mark.parametrize(
    'skill',
    [
        pytest.param(MoveGripper(Side.LEFT, Pose.from_xyz(1.2, 0.2, 1.0)), id='move_gripper'),
        pytest.param(Place(Pose.from_xyz(1.2, 0.2, 1.0)), id='place'),
    ],
)
def test_a_target_inside_a_keep_out_region_is_aborted(skill):
    """The stub stops a real command, and says which region and where."""
    layer = SafetyLayer(limits=make_limits(), collision_guard=KeepOutBoxGuard((STOVE,)))

    verdict = layer.filter(skill, make_state())

    assert isinstance(verdict, SafetyEvent)
    assert verdict.kind is SafetyEventKind.COLLISION_RISK
    assert 'stove_top' in verdict.detail
    assert not verdict.is_clamp, 'geometry is aborted, never rewritten'


def test_a_target_outside_every_region_passes_through_unchanged():
    """A guard that vetoes nothing must not disturb the pass-through identity."""
    layer = SafetyLayer(
        limits=make_limits(), collision_guard=KeepOutBoxGuard((STOVE, BELOW_FLOOR)))
    skill = Place(Pose.from_xyz(0.4, 2.0, 0.75))

    verdict = layer.filter(skill, make_state())

    assert isinstance(verdict, ClampedCall)
    assert verdict.skill is skill


@pytest.mark.parametrize(
    'skill',
    [
        pytest.param(NavigateTo('kitchen'), id='navigate_to'),
        pytest.param(Grasp('mug_1'), id='grasp'),
        pytest.param(ExtendColumn(0.5), id='extend_column'),
        pytest.param(CloseGripper(Side.LEFT), id='close_gripper'),
    ],
)
def test_skills_with_no_cartesian_target_are_not_geometry_this_stub_can_judge(skill):
    """Known limit, stated in code: only a commanded pose is checked.

    A ``Grasp`` moves through space too, but its target is an object id, and
    resolving that to a swept volume is the real-geometry feature, not this.
    """
    layer = SafetyLayer(
        limits=make_limits(), collision_guard=KeepOutBoxGuard((STOVE, BELOW_FLOOR)))

    assert target_pose(skill) is None
    assert isinstance(layer.filter(skill, make_state()), ClampedCall)


def test_the_shipped_keep_out_regions_stop_a_target_below_the_floor():
    """The default configuration is not decorative: it holds a real command."""
    limits = SafetyLimits.defaults()
    layer = SafetyLayer(limits=limits, collision_guard=KeepOutBoxGuard.from_limits(limits))

    verdict = layer.filter(Place(Pose.from_xyz(0.4, 2.0, -0.5)), make_state())

    assert isinstance(verdict, SafetyEvent)
    assert verdict.kind is SafetyEventKind.COLLISION_RISK
    assert isinstance(layer.filter(Place(Pose.from_xyz(0.4, 2.0, 0.75)), make_state()),
                      ClampedCall)


def test_an_injected_guard_is_consulted_for_every_skill():
    """The seam is real: whatever is injected decides, for anything it likes."""
    seen = []

    class RefuseEverything:
        """A guard that objects to every skill, to prove the hook is wired."""

        def check(self, skill, state):
            """Record the skill and veto it."""
            seen.append(skill)
            return SafetyEvent(kind=SafetyEventKind.COLLISION_RISK, detail='no')

    layer = SafetyLayer(limits=make_limits(), collision_guard=RefuseEverything())

    assert isinstance(layer.filter(NavigateTo('kitchen'), make_state()), SafetyEvent)
    assert isinstance(layer.filter(ExtendColumn(9.0), make_state()), SafetyEvent)
    assert len(seen) == 2


def test_a_guard_that_breaks_the_protocol_is_not_quietly_believed():
    """A truthy non-event must not be mistaken for either verdict."""
    class BadGuard:
        """A guard returning the wrong type, as a buggy integration would."""

        def check(self, skill, state):
            """Return prose where an event belongs."""
            return 'too close'

    layer = SafetyLayer(limits=make_limits(), collision_guard=BadGuard())

    with pytest.raises(TypeError, match='BadGuard.check'):
        layer.filter(NavigateTo('kitchen'), make_state())


def test_a_guard_cannot_be_built_on_something_that_is_not_a_region():
    """Bad geometry config fails at construction, not mid-motion."""
    with pytest.raises(TypeError):
        KeepOutBoxGuard(('stove_top',))
