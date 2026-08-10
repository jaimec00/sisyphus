# Copyright (c) 2026 Jaime C.
#
# Use of this source code is governed by an MIT-style
# license that can be found in the LICENSE file or at
# https://opensource.org/licenses/MIT.

"""Frozen wire-format fixtures and the drift check that guards them (D18).

``golden/v<N>/<Type>.json`` holds one canonical ``to_dict()`` per public
serializable type, as it looked when schema version ``<N>`` was current.  The
files are a *historical* record, not a snapshot to refresh: they are generated
once, from real ``to_dict()`` calls on the samples below (never hand-typed),
and then left alone.

That is what makes the compat rule from D18 machine-checkable:

* **adding an optional field** leaves every golden key present and unchanged,
  so :func:`schema_drift` stays quiet at the same version -- non-breaking, no
  regeneration, no bump;
* **dropping, renaming or retyping a field** makes a golden key vanish or
  change shape, so the guard fails and keeps failing until the author bumps
  ``SCHEMA_VERSION`` and writes a fresh ``golden/v<N+1>/`` set -- which is the
  moment every binder in the repo gets updated too.

Regenerating (only after a deliberate ``SCHEMA_VERSION`` bump)::

    python src/robot_skills/test/golden_fixtures.py          # write missing files
    python src/robot_skills/test/golden_fixtures.py --force  # overwrite (rare)

The samples are built here rather than reused from ``skill_api_fixtures`` on
purpose: a golden fixture must not move because a shared test builder was
edited for some unrelated test's convenience.
"""

import inspect
import json
from pathlib import Path
import sys
from typing import Any

from robot_skills import (
    CloseGripper,
    ExtendColumn,
    FailureCode,
    Grasp,
    GripperObservation,
    GripperState,
    JsonDict,
    JsonSerializable,
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
    SCHEMA_VERSION,
    Side,
    SkillResult,
)

#: Directory holding one ``v<N>`` sub-directory per schema version ever frozen.
GOLDEN_ROOT = Path(__file__).parent / 'golden'

#: A unit quaternion (a quarter turn about z), so the fixtures stay plausible.
_QUARTER_TURN = Quaternion(0.0, 0.0, 0.7071067811865476, 0.7071067811865476)

_POSE = Pose(Point(1.25, -0.5, 0.75), _QUARTER_TURN)
_MUG_POSE = Pose(Point(1.3, 0.2, 0.9), Quaternion(0.0, 0.0, 0.0, 1.0))

_HELD_MUG = SceneObject('mug_1', 'mug', _MUG_POSE, graspable=True, held_by=Side.LEFT)
_COUNTER = SceneObject(
    'counter_1', 'counter', Pose(Point(1.5, 0.0, 0.5), Quaternion(0.0, 0.0, 0.0, 1.0)),
    graspable=False,
)
_LEFT_GRIPPER = GripperObservation(
    side=Side.LEFT,
    state=GripperState.CLOSED,
    pose=_MUG_POSE,
    held_object_id='mug_1',
    grasped=True,
)
_RIGHT_GRIPPER = GripperObservation(
    side=Side.RIGHT,
    state=GripperState.OPEN,
    pose=_POSE,
    held_object_id=None,
    grasped=False,
)
_ROBOT_STATE = RobotState(
    pose=Pose(Point(1.0, 2.0, 0.0), Quaternion(0.0, 0.0, 0.7071, 0.7071)),
    column_height=0.4,
    grippers=(_LEFT_GRIPPER, _RIGHT_GRIPPER),
    location='kitchen',
)
_OBSERVATION = Observation(
    robot=_ROBOT_STATE,
    objects=(_COUNTER, _HELD_MUG),
    known_locations=('kitchen', 'table'),
)

#: One canonical instance per public serializable type, keyed by type name.
#:
#: Every optional field is filled in with a *non-null* value wherever the type
#: allows one, so the frozen fixture pins each field's wire type and not just
#: its name -- a ``null`` would still round-trip after a retype.
GOLDEN_SAMPLES: dict[str, JsonSerializable] = {
    'Point': Point(0.25, -1.5, 0.75),
    'Quaternion': _QUARTER_TURN,
    'Pose': _POSE,
    'NavigateTo': NavigateTo('kitchen'),
    'MoveGripper': MoveGripper(Side.RIGHT, _POSE),
    'Grasp': Grasp('mug_1', Side.LEFT),
    'Place': Place(_POSE, Side.RIGHT),
    'ExtendColumn': ExtendColumn(0.42),
    'OpenGripper': OpenGripper(Side.LEFT),
    'CloseGripper': CloseGripper(Side.RIGHT),
    'SceneObject': _HELD_MUG,
    'GripperObservation': _LEFT_GRIPPER,
    'RobotState': _ROBOT_STATE,
    'Observation': _OBSERVATION,
    'SkillResult': SkillResult.failure(
        Grasp('bowl_1', Side.RIGHT),
        _OBSERVATION,
        FailureCode.OUT_OF_REACH,
        "cannot grasp 'bowl_1': it is 1.20 m from the right shoulder",
    ),
}


def golden_dir(version: int = SCHEMA_VERSION) -> Path:
    """Return the directory holding the fixtures frozen at ``version``."""
    return GOLDEN_ROOT / f'v{version}'


def golden_path(name: str, version: int = SCHEMA_VERSION) -> Path:
    """Return the fixture file for one type at ``version``."""
    return golden_dir(version) / f'{name}.json'


def load_golden(name: str, version: int = SCHEMA_VERSION) -> JsonDict:
    """Load one frozen fixture as a plain dict.

    A missing file is the shape a fresh ``SCHEMA_VERSION`` bump takes, so it
    answers with the instruction the author needs rather than a bare
    ``FileNotFoundError`` naming a path they have never seen.
    """
    path = golden_path(name, version)
    try:
        text = path.read_text(encoding='utf-8')
    except FileNotFoundError as exc:
        raise FileNotFoundError(
            f'no frozen fixture at {path}.\n'
            f'If SCHEMA_VERSION was just bumped to {version}, freeze the new wire '
            'form with:\n'
            '    python src/robot_skills/test/golden_fixtures.py\n'
            'which writes the missing golden/v'
            f'{version}/ files from real to_dict() calls.  If instead a fixture '
            'went missing at an unchanged version, restore it from git: a frozen '
            'record is not something to regenerate.'
        ) from exc
    return json.loads(text)


def public_serializable_types() -> dict[str, type[JsonSerializable]]:
    """Return every concrete, public :class:`JsonSerializable` in robot_skills.

    Discovered rather than listed, so a newly added type shows up here (and
    then fails the completeness test) instead of quietly shipping unguarded.
    Abstract bases (``Skill``) and private shared bases (``_GripperSkill``) are
    excluded: they have no wire form of their own.
    """
    found: dict[str, type[JsonSerializable]] = {}
    pending = list(JsonSerializable.__subclasses__())
    while pending:
        cls = pending.pop()
        pending.extend(cls.__subclasses__())
        if not cls.__module__.startswith('robot_skills'):
            continue
        if inspect.isabstract(cls) or cls.__name__.startswith('_'):
            continue
        found[cls.__name__] = cls
    return found


def json_type_name(value: Any) -> str:
    """Return the JSON type of ``value``, keeping ``bool`` distinct from ``int``.

    ``isinstance(True, int)`` is true in Python, so a plain ``type()`` or
    ``isinstance`` check would let a field retyped from ``bool`` to ``int``
    (or the reverse) slip past the guard unnoticed.
    """
    if value is None:
        return 'null'
    if isinstance(value, bool):
        return 'bool'
    if isinstance(value, int):
        return 'int'
    if isinstance(value, float):
        return 'float'
    if isinstance(value, str):
        return 'str'
    if isinstance(value, dict):
        return 'object'
    if isinstance(value, list):
        return 'array'
    return type(value).__name__


def schema_drift(actual: Any, golden: Any, *, path: str = '<root>') -> list[str]:
    """Return every breaking difference between ``actual`` and a frozen fixture.

    Breaking, per D18: a golden key missing from ``actual`` (dropped or
    renamed), a value whose JSON type changed (retyped), or a value that
    changed.  Keys present only in ``actual`` are *not* breaking -- that is an
    additive optional field, which the rule allows at the same version -- so
    they are deliberately not reported.

    Values are frozen as well as shapes, on purpose: a fixture is what a *given*
    sample serialized to, so a silently changed constant (a different quaternion
    normalisation, a renamed enum value, a unit change from metres to
    centimetres) is drift the brain would act on and belongs in this report.
    Such a failure reads ``value changed``; a shape failure names the types.
    """
    if json_type_name(actual) != json_type_name(golden):
        return [
            f'{path}: retyped {json_type_name(golden)} -> {json_type_name(actual)} '
            f'(golden={golden!r}, actual={actual!r})'
        ]
    if isinstance(golden, dict):
        drift: list[str] = []
        for key, expected in golden.items():
            if key not in actual:
                drift.append(f'{path}.{key}: dropped or renamed (golden={expected!r})')
                continue
            drift.extend(schema_drift(actual[key], expected, path=f'{path}.{key}'))
        return drift
    if isinstance(golden, list):
        if len(actual) != len(golden):
            return [f'{path}: list length {len(golden)} -> {len(actual)}']
        drift = []
        for index, expected in enumerate(golden):
            drift.extend(schema_drift(actual[index], expected, path=f'{path}[{index}]'))
        return drift
    if actual != golden:
        return [f'{path}: value changed {golden!r} -> {actual!r}']
    return []


def write_golden(version: int = SCHEMA_VERSION, *, force: bool = False) -> list[Path]:
    """Write the fixtures for ``version`` from real ``to_dict()`` calls.

    Existing files are left alone unless ``force`` is passed: a frozen fixture
    that can be silently refreshed guards nothing.
    """
    target = golden_dir(version)
    target.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    for name, sample in sorted(GOLDEN_SAMPLES.items()):
        path = golden_path(name, version)
        if path.exists() and not force:
            continue
        path.write_text(
            json.dumps(sample.to_dict(), indent=2, sort_keys=True) + '\n',
            encoding='utf-8',
        )
        written.append(path)
    return written


def main(argv: list[str]) -> int:
    """Regenerate the fixtures for the current schema version."""
    force = '--force' in argv
    written = write_golden(force=force)
    for path in written:
        print(f'wrote {path}')
    if not written:
        print(f'{golden_dir()} is already complete (pass --force to overwrite)')
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
