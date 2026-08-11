# Copyright (c) 2026 Jaime C.
#
# Use of this source code is governed by an MIT-style
# license that can be found in the LICENSE file or at
# https://opensource.org/licenses/MIT.

"""The numbers: a strictly parsed, YAML-backed limit set.

The shipped ``limits.yaml`` is the **single source of the defaults**.  Nothing
in this module hard-codes a metre, a metre-per-second or a newton: two sources
of a safety number drift, and the drift would be silent.  Tuning the robot's
envelope is a one-file edit, reviewable on its own.

Parsing is deliberately unforgiving -- unknown keys, missing keys, non-finite
numbers, non-positive caps and an inverted column range are all load errors
naming the offending key.  A typo in a limits file must never resolve to a
default: "the config silently didn't apply" is exactly how a safety layer stops
being one.
"""

from dataclasses import dataclass
from functools import lru_cache
from importlib import resources
from types import MappingProxyType
from typing import Any, Mapping

from robot_safety.state import MotionAxis
from robot_skills import Point
from robot_skills.validation import as_finite_float, as_identifier
import yaml

__all__ = [
    'ColumnLimits',
    'DEFAULT_LIMITS_RESOURCE',
    'KeepOutBox',
    'MotionLimits',
    'SafetyConfigError',
    'SafetyLimits',
]

#: Name of the YAML file shipped inside the importable ``robot_safety`` package.
#:
#: It lives beside the code rather than in ``share/`` on purpose: a file inside
#: the package directory is readable from a source checkout and from a
#: symlink-installed build alike, with no ROS graph, no ament index and no
#: "rebuild before the tests can see it" step.
DEFAULT_LIMITS_RESOURCE = 'limits.yaml'


class SafetyConfigError(ValueError):
    """Raised when a limits configuration is malformed, incomplete or unsafe."""


def _as_mapping(value: Any, *, context: str) -> Mapping[str, Any]:
    """Return ``value`` as a mapping with string keys, or raise."""
    if not isinstance(value, Mapping):
        raise SafetyConfigError(
            f'{context}: expected a mapping, got {type(value).__name__}')
    for key in value:
        if not isinstance(key, str):
            raise SafetyConfigError(
                f'{context}: keys must be strings, got {type(key).__name__} ({key!r})')
    return value


def _check_keys(
    data: Mapping[str, Any],
    *,
    required: tuple[str, ...],
    optional: tuple[str, ...] = (),
    context: str,
) -> None:
    """Reject a mapping that is missing a required key or carries an unknown one."""
    missing = [key for key in required if key not in data]
    if missing:
        raise SafetyConfigError(f'{context}: missing required key(s): {", ".join(missing)}')
    allowed = set(required) | set(optional)
    unknown = sorted(key for key in data if key not in allowed)
    if unknown:
        raise SafetyConfigError(
            f'{context}: unknown key(s): {", ".join(unknown)} '
            f'(allowed: {", ".join(sorted(allowed))})')


def _get_float(data: Mapping[str, Any], key: str, *, context: str) -> float:
    """Return a finite float from ``data[key]``, or raise naming the key."""
    try:
        return as_finite_float(data[key], name=f'{context}.{key}')
    except (TypeError, ValueError) as exc:
        raise SafetyConfigError(f'{context}.{key}: {exc}') from exc


def _as_positive(value: Any, *, name: str) -> float:
    """Return ``value`` as a strictly positive finite float, or raise.

    Caps are positive by definition.  A zero cap would abort every motion the
    instant any reading arrived, which is what the e-stop is for; reading it as
    a deliberate policy rather than a config slip would be the wrong guess.

    Lives on the *type*, not on the parser, so a cap built in Python is held to
    the same rule as one loaded from YAML -- the file is only one of the ways
    a limit set comes into being.
    """
    try:
        result = as_finite_float(value, name=name)
    except (TypeError, ValueError) as exc:
        raise SafetyConfigError(f'{name}: {exc}') from exc
    if result <= 0.0:
        raise SafetyConfigError(f'{name}: must be a positive number, got {result!r}')
    return result


def _get_positive(data: Mapping[str, Any], key: str, *, context: str) -> float:
    """Return a strictly positive finite float from ``data[key]``, naming the key."""
    return _as_positive(_get_float(data, key, context=context), name=f'{context}.{key}')


@dataclass(frozen=True)
class ColumnLimits:
    """Travel range of the vertical lift column, in metres."""

    min_height: float
    max_height: float

    def __post_init__(self) -> None:
        object.__setattr__(
            self, 'min_height', as_finite_float(self.min_height, name='min_height'))
        object.__setattr__(
            self, 'max_height', as_finite_float(self.max_height, name='max_height'))
        if self.min_height >= self.max_height:
            raise SafetyConfigError(
                f'column: min_height ({self.min_height!r}) must be below '
                f'max_height ({self.max_height!r})')

    def clamp(self, height: float) -> float:
        """Return ``height`` pulled into the travel range."""
        return min(max(height, self.min_height), self.max_height)

    def violated_bound(self, height: float) -> float | None:
        """Return the bound ``height`` breaches, or ``None`` when it is in range."""
        if height < self.min_height:
            return self.min_height
        if height > self.max_height:
            return self.max_height
        return None

    @classmethod
    def from_mapping(cls, data: Any, *, context: str = 'column') -> 'ColumnLimits':
        """Build column limits from the ``column`` section of a limits file."""
        data = _as_mapping(data, context=context)
        _check_keys(data, required=('min_height', 'max_height'), context=context)
        return cls(
            min_height=_get_float(data, 'min_height', context=context),
            max_height=_get_float(data, 'max_height', context=context),
        )


@dataclass(frozen=True)
class MotionLimits:
    """The dynamic envelope: per-axis speed caps and the jaw-force ceiling.

    This is the part of the limit set that rides out with an accepted call
    (:attr:`~robot_safety.layer.ClampedCall.limits`).  The safety layer checks
    *measured* values against it; the backend is contractually required to
    honour it as a *commanded* envelope, since below this seam is the only
    place that can actually rate-limit a trajectory.
    """

    velocities: Mapping[MotionAxis, float]
    max_gripper_force: float

    def __post_init__(self) -> None:
        velocities = dict(self.velocities)
        for axis, cap in velocities.items():
            if not isinstance(axis, MotionAxis):
                raise TypeError(
                    'MotionLimits.velocities keys must be MotionAxis members, '
                    f'got {type(axis).__name__}')
            velocities[axis] = _as_positive(cap, name=f'velocity.{axis.value}')
        missing = [axis.value for axis in MotionAxis if axis not in velocities]
        if missing:
            raise SafetyConfigError(
                f'velocity: missing cap(s) for axis/axes: {", ".join(missing)}')
        object.__setattr__(self, 'velocities', MappingProxyType(velocities))
        object.__setattr__(
            self,
            'max_gripper_force',
            _as_positive(self.max_gripper_force, name='gripper.max_force'),
        )

    def velocity_cap(self, axis: MotionAxis) -> float:
        """Return the speed cap for one axis in m/s (every axis has one)."""
        return self.velocities[axis]

    @classmethod
    def from_mapping(cls, velocity: Any, gripper: Any) -> 'MotionLimits':
        """Build the envelope from the ``velocity`` and ``gripper`` sections."""
        velocity = _as_mapping(velocity, context='velocity')
        _check_keys(
            velocity, required=tuple(axis.value for axis in MotionAxis), context='velocity')
        gripper = _as_mapping(gripper, context='gripper')
        _check_keys(gripper, required=('max_force',), context='gripper')
        return cls(
            velocities={
                axis: _get_positive(velocity, axis.value, context='velocity')
                for axis in MotionAxis
            },
            max_gripper_force=_get_positive(gripper, 'max_force', context='gripper'),
        )


#: The per-axis bound keys of a keep-out box, in report order.
_BOX_BOUNDS = ('x_min', 'x_max', 'y_min', 'y_max', 'z_min', 'z_max')


@dataclass(frozen=True)
class KeepOutBox:
    """A named axis-aligned world-frame region a target pose may not fall in.

    Every bound is optional and ``None`` means *unbounded on that side*, so a
    box can express a half-space ("anything below the floor") without pretending
    to a finite extent it does not have.  At least one bound must be set --
    a box bounded nowhere would contain the entire world.

    This is stub geometry by design: real collision geometry (meshes, a robot
    model, swept volumes) is a later feature.  What ships here is the *seam*
    plus an implementation crude enough to be obviously crude and precise enough
    to actually stop a command.
    """

    label: str
    x_min: float | None = None
    x_max: float | None = None
    y_min: float | None = None
    y_max: float | None = None
    z_min: float | None = None
    z_max: float | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, 'label', as_identifier(self.label, name='keep_out_boxes.label'))
        for name in _BOX_BOUNDS:
            value = getattr(self, name)
            if value is None:
                continue
            object.__setattr__(
                self, name, as_finite_float(value, name=f'keep_out_boxes.{name}'))
        if all(getattr(self, name) is None for name in _BOX_BOUNDS):
            raise SafetyConfigError(
                f'keep_out_boxes[{self.label!r}]: needs at least one bound; a box with '
                'none would exclude the entire world')
        for axis in ('x', 'y', 'z'):
            low = getattr(self, f'{axis}_min')
            high = getattr(self, f'{axis}_max')
            if low is not None and high is not None and low >= high:
                raise SafetyConfigError(
                    f'keep_out_boxes[{self.label!r}]: {axis}_min ({low!r}) must be below '
                    f'{axis}_max ({high!r})')

    def contains(self, point: Point) -> bool:
        """Return whether ``point`` lies inside this region (bounds inclusive)."""
        if not isinstance(point, Point):
            raise TypeError(f'point must be a Point, got {type(point).__name__}')
        for axis in ('x', 'y', 'z'):
            value = getattr(point, axis)
            low = getattr(self, f'{axis}_min')
            high = getattr(self, f'{axis}_max')
            if low is not None and value < low:
                return False
            if high is not None and value > high:
                return False
        return True

    @classmethod
    def from_mapping(cls, data: Any, *, context: str = 'keep_out_boxes') -> 'KeepOutBox':
        """Build one keep-out box from a ``keep_out_boxes`` list entry."""
        data = _as_mapping(data, context=context)
        _check_keys(data, required=('label',), optional=_BOX_BOUNDS, context=context)
        label = data['label']
        if not isinstance(label, str):
            raise SafetyConfigError(
                f'{context}.label: expected a string, got {type(label).__name__}')
        bounds = {
            name: _get_float(data, name, context=f'{context}[{label!r}]')
            for name in _BOX_BOUNDS if name in data
        }
        return cls(label=label, **bounds)


@dataclass(frozen=True)
class SafetyLimits:
    """The whole configured envelope: column range, dynamics, keep-out regions."""

    column: ColumnLimits
    motion: MotionLimits
    keep_out_boxes: tuple[KeepOutBox, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.column, ColumnLimits):
            raise TypeError(
                'SafetyLimits.column must be a ColumnLimits, '
                f'got {type(self.column).__name__}')
        if not isinstance(self.motion, MotionLimits):
            raise TypeError(
                'SafetyLimits.motion must be a MotionLimits, '
                f'got {type(self.motion).__name__}')
        boxes = tuple(self.keep_out_boxes)
        for box in boxes:
            if not isinstance(box, KeepOutBox):
                raise TypeError(
                    'SafetyLimits.keep_out_boxes must contain KeepOutBox values, '
                    f'got {type(box).__name__}')
        labels = [box.label for box in boxes]
        duplicates = sorted({name for name in labels if labels.count(name) > 1})
        if duplicates:
            raise SafetyConfigError(
                f'keep_out_boxes: duplicate label(s): {", ".join(duplicates)}')
        object.__setattr__(self, 'keep_out_boxes', boxes)

    @classmethod
    def defaults(cls) -> 'SafetyLimits':
        """Return the limits shipped in ``robot_safety/limits.yaml``."""
        return _default_limits()

    @classmethod
    def from_mapping(cls, data: Any, *, context: str = 'limits') -> 'SafetyLimits':
        """Build a limit set from an already-parsed mapping."""
        data = _as_mapping(data, context=context)
        _check_keys(
            data,
            required=('column', 'velocity', 'gripper'),
            optional=('keep_out_boxes',),
            context=context,
        )
        raw_boxes = data.get('keep_out_boxes', [])
        if not isinstance(raw_boxes, (list, tuple)):
            raise SafetyConfigError(
                f'{context}.keep_out_boxes: expected a list, got {type(raw_boxes).__name__}')
        return cls(
            column=ColumnLimits.from_mapping(data['column']),
            motion=MotionLimits.from_mapping(data['velocity'], data['gripper']),
            keep_out_boxes=tuple(KeepOutBox.from_mapping(item) for item in raw_boxes),
        )

    @classmethod
    def from_yaml(cls, text: str, *, source: str = '<string>') -> 'SafetyLimits':
        """Build a limit set from YAML text (``yaml.safe_load``, never ``load``)."""
        try:
            data = yaml.safe_load(text)
        except yaml.YAMLError as exc:
            raise SafetyConfigError(f'{source}: not valid YAML: {exc}') from exc
        if data is None:
            raise SafetyConfigError(f'{source}: empty limits file')
        return cls.from_mapping(data, context=source)


@lru_cache(maxsize=1)
def _default_limits() -> SafetyLimits:
    """Load, validate and memoize the shipped default limits.

    Memoized because every default-constructed :class:`SafetyLayer` asks for
    it; safe to share because the whole limit set is immutable.
    """
    resource = resources.files('robot_safety') / DEFAULT_LIMITS_RESOURCE
    return SafetyLimits.from_yaml(
        resource.read_text(encoding='utf-8'), source=DEFAULT_LIMITS_RESOURCE)
