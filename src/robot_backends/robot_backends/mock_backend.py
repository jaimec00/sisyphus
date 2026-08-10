# Copyright (c) 2026 Jaime C.
#
# Use of this source code is governed by an MIT-style
# license that can be found in the LICENSE file or at
# https://opensource.org/licenses/MIT.

"""An in-memory robot: the first (and simplest) ``RobotBackend``.

The Mock replaces physics with a small book-keeping model -- where the base is,
how high the column is, what each gripper holds, where every object is -- and
mutates it plausibly in response to skills.  That is enough to run the whole
perceive -> act -> observe loop end to end with no simulator and no hardware,
which is what CLAUDE.md invariant 2 ("new code must work against Mock first")
asks for.

Determinism: there is no clock and no random number generator anywhere in this
module.  The same world plus the same sequence of skills always produces the
same observations, byte for byte, so tests can assert on exact values.

Failure discipline: every handler validates *before* it mutates, by raising
``_SkillRefused``; :meth:`MockBackend.execute` turns that into a failed
:class:`~robot_skills.result.SkillResult`.  A refused skill therefore cannot
leave the world half-changed.
"""

from dataclasses import dataclass
from typing import Callable, Mapping, NoReturn

from robot_backends.interface import RobotBackend
from robot_backends.mock_world import default_world, MockWorld, ObjectSpec
from robot_skills import (
    CloseGripper,
    ExtendColumn,
    FailureCode,
    Grasp,
    GripperObservation,
    GripperState,
    MoveGripper,
    NavigateTo,
    Observation,
    OpenGripper,
    Place,
    Point,
    Pose,
    Quaternion,
    RobotState,
    SceneObject,
    Side,
    SIDE_ORDER,
    Skill,
    SkillResult,
)

__all__ = ['MockBackend']


class _SkillRefused(Exception):
    """Internal signal: a skill cannot run; nothing has been mutated."""

    def __init__(self, code: FailureCode, reason: str) -> None:
        super().__init__(reason)
        self.code = code
        self.reason = reason


@dataclass
class _MockGripper:
    """Mutable per-gripper state: jaws, arm posture, and what is held."""

    state: GripperState
    offset: Point
    orientation: Quaternion
    held_object_id: str | None = None


@dataclass
class _MockObject:
    """Mutable per-object state: an :class:`ObjectSpec` that can move and be held."""

    object_id: str
    label: str
    pose: Pose
    graspable: bool
    held_by: Side | None = None

    @classmethod
    def from_spec(cls, spec: ObjectSpec) -> '_MockObject':
        """Build mutable object state from an immutable world spec entry."""
        return cls(
            object_id=spec.object_id,
            label=spec.label,
            pose=spec.pose,
            graspable=spec.graspable,
        )


class MockBackend(RobotBackend):
    """A deterministic, in-memory robot backend driven by the skill API.

    Example::

        backend = MockBackend()
        backend.execute(NavigateTo('kitchen'))
        result = backend.execute(Grasp('mug_1'))
        result.observation.robot.gripper(Side.LEFT).held_object_id  # 'mug_1'

    Pass a custom :class:`~robot_backends.mock_world.MockWorld` to test against
    a different scene; :meth:`reset` always returns to that same seed world.
    """

    def __init__(self, world: MockWorld | None = None) -> None:
        """Create a backend seeded from ``world`` (the demo apartment by default)."""
        if world is not None and not isinstance(world, MockWorld):
            raise TypeError(f'world must be a MockWorld, got {type(world).__name__}')
        self._world = world if world is not None else default_world()
        self._handlers: Mapping[type[Skill], Callable[..., str | None]] = {
            NavigateTo: self._navigate_to,
            MoveGripper: self._move_gripper,
            Grasp: self._grasp,
            Place: self._place,
            ExtendColumn: self._extend_column,
            OpenGripper: self._open_gripper,
            CloseGripper: self._close_gripper,
        }
        self.reset()

    @property
    def world(self) -> MockWorld:
        """Return the immutable world this backend was seeded from."""
        return self._world

    # -- RobotBackend ------------------------------------------------------

    def reset(self) -> Observation:
        """Restore the seed world and return the resulting observation."""
        model = self._world.robot
        self._base_pose = self._world.start_pose
        self._location: str | None = self._world.start_location
        self._column_height = self._world.start_column_height
        self._grippers: dict[Side, _MockGripper] = {
            side: _MockGripper(
                state=GripperState.OPEN,
                offset=model.home_gripper_offset,
                orientation=Quaternion.identity(),
            )
            for side in SIDE_ORDER
        }
        self._objects: dict[str, _MockObject] = {
            spec.object_id: _MockObject.from_spec(spec) for spec in self._world.objects
        }
        return self.get_observation()

    def get_observation(self) -> Observation:
        """Return an immutable snapshot of the robot and the scene."""
        return Observation(
            robot=RobotState(
                pose=self._base_pose,
                column_height=self._column_height,
                grippers=tuple(self._gripper_observation(side) for side in SIDE_ORDER),
                location=self._location,
            ),
            objects=tuple(
                SceneObject(
                    object_id=item.object_id,
                    label=item.label,
                    pose=item.pose,
                    graspable=item.graspable,
                    held_by=item.held_by,
                )
                for item in sorted(self._objects.values(), key=lambda o: o.object_id)
            ),
            known_locations=tuple(sorted(self._world.locations)),
        )

    def execute(self, skill: Skill) -> SkillResult:
        """Execute one skill, returning its status and a fresh observation."""
        if not isinstance(skill, Skill):
            raise TypeError(
                f'execute() expects a Skill, got {type(skill).__name__}')
        handler = self._handler_for(skill)
        if handler is None:
            return SkillResult.failure(
                skill,
                self.get_observation(),
                FailureCode.UNSUPPORTED_SKILL,
                f'the mock backend does not implement skill {skill.name!r}',
            )
        try:
            note = handler(skill)
        except _SkillRefused as refusal:
            return SkillResult.failure(
                skill, self.get_observation(), refusal.code, refusal.reason)
        self._carry_held_objects()
        return SkillResult.ok(skill, self.get_observation(), note)

    # -- skill handlers ----------------------------------------------------

    def _navigate_to(self, skill: NavigateTo) -> str | None:
        """Drive the base to a named location."""
        pose = self._world.locations.get(skill.location)
        if pose is None:
            raise _SkillRefused(
                FailureCode.UNKNOWN_LOCATION,
                f'unknown location {skill.location!r}; known locations: '
                f'{", ".join(sorted(self._world.locations))}',
            )
        already_there = self._location == skill.location
        self._base_pose = pose
        self._location = skill.location
        return f'already at {skill.location!r}' if already_there else None

    def _move_gripper(self, skill: MoveGripper) -> str | None:
        """Move one gripper to a Cartesian pose within its reach."""
        offset = self._require_reachable(skill.side, skill.pose, 'move the gripper to')
        gripper = self._grippers[skill.side]
        gripper.offset = offset
        gripper.orientation = skill.pose.orientation
        return None

    def _grasp(self, skill: Grasp) -> str | None:
        """Close a free gripper around a present, graspable, reachable object."""
        item = self._objects.get(skill.object_id)
        if item is None:
            raise _SkillRefused(
                FailureCode.UNKNOWN_OBJECT,
                f'no object {skill.object_id!r} in the scene; perceived objects: '
                f'{", ".join(sorted(self._objects))}',
            )
        if not item.graspable:
            raise _SkillRefused(
                FailureCode.NOT_GRASPABLE,
                f'object {item.object_id!r} ({item.label}) is not graspable',
            )
        if item.held_by is not None:
            raise _SkillRefused(
                FailureCode.OBJECT_ALREADY_HELD,
                f'object {item.object_id!r} is already held by the '
                f'{item.held_by.value} gripper',
            )
        side, offset = self._resolve_grasping_side(
            skill.side, item.pose, f'grasp {item.object_id!r}')

        gripper = self._grippers[side]
        gripper.offset = offset
        gripper.orientation = item.pose.orientation
        gripper.state = GripperState.CLOSED
        gripper.held_object_id = item.object_id
        item.held_by = side
        return None

    def _place(self, skill: Place) -> str | None:
        """Put the held object down at a reachable pose and open the gripper."""
        side = self._resolve_holding_side(skill.side)
        gripper = self._grippers[side]
        held_id = gripper.held_object_id
        if held_id is None:  # unreachable: _resolve_holding_side guarantees a load
            raise _SkillRefused(
                FailureCode.GRIPPER_EMPTY,
                f'the {side.value} gripper is empty, there is nothing to place',
            )
        item = self._objects[held_id]
        offset = self._require_reachable(side, skill.pose, f'place {held_id!r}')

        gripper.offset = offset
        gripper.orientation = skill.pose.orientation
        gripper.state = GripperState.OPEN
        gripper.held_object_id = None
        item.pose = skill.pose
        item.held_by = None
        return f'released {held_id!r} from the {side.value} gripper'

    def _extend_column(self, skill: ExtendColumn) -> str | None:
        """Set the lift column height, carrying the arms (and any load) with it."""
        model = self._world.robot
        if not model.min_column_height <= skill.height <= model.max_column_height:
            raise _SkillRefused(
                FailureCode.OUT_OF_RANGE,
                f'column height {skill.height:.2f} m is outside the column range '
                f'{model.column_range_text()}',
            )
        self._column_height = skill.height
        return None

    def _open_gripper(self, skill: OpenGripper) -> str | None:
        """Open a gripper, releasing anything it holds where the gripper is."""
        gripper = self._grippers[skill.side]
        released = gripper.held_object_id
        if released is not None:
            item = self._objects[released]
            item.pose = self._gripper_pose(skill.side)
            item.held_by = None
            gripper.held_object_id = None
        was_open = gripper.state is GripperState.OPEN
        gripper.state = GripperState.OPEN
        if released is not None:
            return f'dropped {released!r} from the {skill.side.value} gripper'
        return f'the {skill.side.value} gripper was already open' if was_open else None

    def _close_gripper(self, skill: CloseGripper) -> str | None:
        """Close a gripper; closing on thin air grips nothing (use Grasp to pick up)."""
        gripper = self._grippers[skill.side]
        was_closed = gripper.state is GripperState.CLOSED
        gripper.state = GripperState.CLOSED
        if was_closed:
            return f'the {skill.side.value} gripper was already closed'
        return None

    # -- world-model helpers ----------------------------------------------

    def _handler_for(self, skill: Skill) -> Callable[..., str | None] | None:
        """Return the handler for a skill type, honouring subclasses."""
        for skill_type in type(skill).__mro__:
            handler = self._handlers.get(skill_type)
            if handler is not None:
                return handler
        return None

    def _shoulder(self, side: Side) -> Point:
        """Return the world-frame shoulder point of one arm."""
        return self._world.robot.shoulder(self._base_pose, self._column_height, side)

    def _gripper_pose(self, side: Side) -> Pose:
        """Return the world-frame pose of one gripper."""
        gripper = self._grippers[side]
        return Pose(
            position=self._shoulder(side) + gripper.offset,
            orientation=gripper.orientation,
        )

    def _gripper_observation(self, side: Side) -> GripperObservation:
        """Return the reportable state of one gripper."""
        gripper = self._grippers[side]
        return GripperObservation(
            side=side,
            state=gripper.state,
            pose=self._gripper_pose(side),
            held_object_id=gripper.held_object_id,
        )

    def _carry_held_objects(self) -> None:
        """Keep every held object glued to the gripper holding it."""
        for side in SIDE_ORDER:
            held_id = self._grippers[side].held_object_id
            if held_id is not None:
                self._objects[held_id].pose = self._gripper_pose(side)

    def _reach_offset(self, side: Side, target: Pose) -> Point | None:
        """Return the arm offset reaching ``target``, or ``None`` if too far."""
        offset = target.position - self._shoulder(side)
        return offset if offset.norm() <= self._world.robot.reach_radius else None

    def _require_reachable(self, side: Side, target: Pose, action: str) -> Point:
        """Return the arm offset reaching ``target``, or refuse the skill."""
        offset = self._reach_offset(side, target)
        if offset is None:
            distance = (target.position - self._shoulder(side)).norm()
            reach = self._world.robot.reach_radius
            at = 'nowhere' if self._location is None else repr(self._location)
            raise _SkillRefused(
                FailureCode.OUT_OF_REACH,
                f'cannot {action}: it is {distance:.2f} m from the {side.value} '
                f'shoulder, beyond the {reach:.2f} m reach (robot is at {at})',
            )
        return offset

    def _resolve_grasping_side(
        self,
        requested: Side | None,
        target: Pose,
        action: str,
    ) -> tuple[Side, Point]:
        """Pick which gripper grasps ``target``, and the arm offset to do it with.

        With a side named, that side must be free and able to reach.  With no
        side named, prefer the first side in ``SIDE_ORDER`` that is *both* free
        and within reach -- the shoulders are far enough apart that an object
        can be reachable by one arm only, and committing to the left arm before
        checking reach would make the brain parse a prose reason and retry.
        Resolution stays deterministic: order is fixed, no distance tie-breaks.
        """
        if requested is not None:
            self._require_free_gripper(requested)
            return requested, self._require_reachable(requested, target, action)

        free = tuple(
            side for side in SIDE_ORDER if self._grippers[side].held_object_id is None)
        if not free:
            self._refuse_both_grippers_occupied()
        for side in free:
            offset = self._reach_offset(side, target)
            if offset is not None:
                return side, offset
        # No free gripper can reach: report the preferred one's distance.
        return free[0], self._require_reachable(free[0], target, action)

    def _require_free_gripper(self, side: Side) -> None:
        """Refuse if the named gripper is already holding something."""
        held = self._grippers[side].held_object_id
        if held is not None:
            raise _SkillRefused(
                FailureCode.GRIPPER_OCCUPIED,
                f'the {side.value} gripper already holds {held!r}',
            )

    def _refuse_both_grippers_occupied(self) -> NoReturn:
        """Refuse a grasp because there is no free gripper at all."""
        holdings = ', '.join(
            f'{side.value} holds {self._grippers[side].held_object_id!r}'
            for side in SIDE_ORDER
        )
        raise _SkillRefused(
            FailureCode.GRIPPER_OCCUPIED, f'both grippers are occupied ({holdings})')

    def _resolve_holding_side(self, requested: Side | None) -> Side:
        """Pick which gripper releases, or refuse if none holds anything."""
        if requested is not None:
            if self._grippers[requested].held_object_id is None:
                raise _SkillRefused(
                    FailureCode.GRIPPER_EMPTY,
                    f'the {requested.value} gripper is empty, there is nothing to place',
                )
            return requested
        for side in SIDE_ORDER:
            if self._grippers[side].held_object_id is not None:
                return side
        raise _SkillRefused(
            FailureCode.GRIPPER_EMPTY,
            'no gripper is holding an object, there is nothing to place',
        )
