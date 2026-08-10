# Copyright (c) 2026 Jaime C.
#
# Use of this source code is governed by an MIT-style
# license that can be found in the LICENSE file or at
# https://opensource.org/licenses/MIT.

"""The D18 wire-format guard: today's to_dict() against the frozen fixtures.

The fixtures in ``golden/v<N>/`` were generated from real ``to_dict()`` calls
(see ``golden_fixtures.py``), never hand-typed, and are then frozen: they
record what version ``<N>`` of the schema looked like.  Comparing against them
catches the drift the existing tests structurally cannot --
``from_dict(to_dict()) == x`` still passes after a field is renamed in both
directions at once, and ``check_keys`` only ever objects to keys that were
*added*.

The comparison is deliberately asymmetric, encoding the compat rule: an added
key is fine at the same version (additive optional field = non-breaking), while
a dropped, renamed or retyped one fails until ``SCHEMA_VERSION`` is bumped and a
new fixture set is written -- the same PR that updates every binder.
"""

import json

from golden_fixtures import (
    golden_dir,
    golden_path,
    GOLDEN_SAMPLES,
    json_type_name,
    load_golden,
    public_serializable_types,
    schema_drift,
)
import pytest
from robot_skills import SCHEMA_VERSION


@pytest.mark.parametrize('name', sorted(GOLDEN_SAMPLES))
def test_the_wire_form_still_matches_the_frozen_fixture(name):
    """No field may be dropped, renamed or retyped without a version bump."""
    drift = schema_drift(GOLDEN_SAMPLES[name].to_dict(), load_golden(name))
    assert drift == [], (
        f'{name} no longer matches golden/v{SCHEMA_VERSION}/{name}.json:\n  '
        + '\n  '.join(drift)
        + '\n\nAdding an optional field is non-breaking and needs no change here. '
          'Anything else is a breaking change: bump SCHEMA_VERSION, regenerate '
          'into the new golden/v<N>/ directory, and update every binder in the '
          'same PR (D18).')


def test_every_public_serializable_type_has_a_golden_fixture():
    """A new type on the seam cannot ship unguarded, nor a fixture outlive its type."""
    discovered = set(public_serializable_types())
    assert discovered == set(GOLDEN_SAMPLES), (
        'GOLDEN_SAMPLES is out of step with the types in robot_skills: '
        f'missing {sorted(discovered - set(GOLDEN_SAMPLES))}, '
        f'stale {sorted(set(GOLDEN_SAMPLES) - discovered)}')
    assert len(discovered) == 15, 'the seam gained or lost a type; is that intended?'

    for name in GOLDEN_SAMPLES:
        assert golden_path(name).is_file(), f'no frozen fixture for {name}'
    on_disk = {path.stem for path in golden_dir().glob('*.json')}
    assert on_disk == set(GOLDEN_SAMPLES)


def test_each_sample_is_an_instance_of_the_type_it_is_filed_under():
    """A fixture generated from the wrong object would freeze the wrong shape."""
    types = public_serializable_types()
    for name, sample in GOLDEN_SAMPLES.items():
        assert type(sample) is types[name]


def test_the_fixtures_are_reachable_and_json_under_the_test_runner():
    """Guard against fixtures that exist in git but are invisible to colcon test."""
    assert golden_dir().is_dir(), f'{golden_dir()} not found from {__file__}'
    for path in sorted(golden_dir().glob('*.json')):
        assert isinstance(json.loads(path.read_text(encoding='utf-8')), dict)


def test_the_guard_flags_a_dropped_or_renamed_field():
    """The drift the round-trip tests cannot see: a field that quietly left."""
    golden = load_golden('GripperObservation')

    dropped = {key: value for key, value in golden.items() if key != 'grasped'}
    assert schema_drift(dropped, golden) == [
        '<root>.grasped: dropped or renamed (golden=True)']

    renamed = {**dropped, 'gripping': True}
    drift = schema_drift(renamed, golden)
    assert len(drift) == 1 and 'grasped' in drift[0], drift

    nested = json.loads(json.dumps(load_golden('Observation')))
    del nested['robot']['grippers'][0]['held_object_id']
    drift = schema_drift(nested, load_golden('Observation'))
    assert drift == ['<root>.robot.grippers[0].held_object_id: dropped or renamed '
                     "(golden='mug_1')"]


def test_the_guard_flags_a_retyped_field():
    """A field keeping its name while changing type breaks every binder silently."""
    golden = load_golden('RobotState')

    stringified = {**golden, 'column_height': '0.4'}
    drift = schema_drift(stringified, golden)
    assert drift == ['<root>.column_height: retyped float -> str '
                     "(golden=0.4, actual='0.4')"]

    # bool is an int in Python; the guard must not be fooled by that.
    gripper = load_golden('GripperObservation')
    drift = schema_drift({**gripper, 'grasped': 1}, gripper)
    assert drift == ['<root>.grasped: retyped bool -> int (golden=True, actual=1)']
    assert json_type_name(True) != json_type_name(1)

    # An object collapsed to a scalar, and a list collapsed to an object.
    drift = schema_drift({**golden, 'pose': 'origin'}, golden)
    assert len(drift) == 1 and 'retyped object -> str' in drift[0], drift
    drift = schema_drift({**golden, 'grippers': {}}, golden)
    assert len(drift) == 1 and 'retyped array -> object' in drift[0], drift


def test_the_guard_flags_a_changed_value_and_a_shortened_list():
    """A fixture is a shape *and* the values that shape carried."""
    golden = load_golden('Observation')

    drift = schema_drift({**golden, 'known_locations': ['kitchen', 'garage']}, golden)
    assert drift == ["<root>.known_locations[1]: value changed 'table' -> 'garage'"]

    drift = schema_drift({**golden, 'objects': golden['objects'][:1]}, golden)
    assert drift == ['<root>.objects: list length 2 -> 1']

    drift = schema_drift({**golden, 'schema_version': 2}, golden)
    assert drift == ['<root>.schema_version: value changed 1 -> 2']


def test_the_guard_allows_an_added_optional_field():
    """Additive = non-breaking (D18): a new key must not force a version bump.

    This is the half of the rule that keeps the guard from being a nuisance --
    and the reason the comparison is one-directional rather than an equality
    assertion on the whole dict.
    """
    golden = load_golden('GripperObservation')

    assert schema_drift({**golden, 'aperture_m': 0.02}, golden) == []
    assert schema_drift({**golden, 'contact_force_n': None}, golden) == []

    nested = json.loads(json.dumps(golden))
    nested['pose']['frame_id'] = 'base_link'
    assert schema_drift(nested, golden) == []

    deep = json.loads(json.dumps(load_golden('SkillResult')))
    deep['observation']['robot']['grippers'][1]['aperture_m'] = 0.08
    assert schema_drift(deep, load_golden('SkillResult')) == []


def test_the_guard_reports_every_drift_at_once():
    """A schema change is reviewed as a whole, not one failure per run."""
    golden = load_golden('SceneObject')
    mutated = {key: value for key, value in golden.items() if key != 'label'}
    mutated['graspable'] = 'yes'
    mutated['object_id'] = 'mug_2'

    drift = schema_drift(mutated, golden)
    assert len(drift) == 3, drift
    assert any('label' in item for item in drift)
    assert any('retyped bool -> str' in item for item in drift)
    assert any('value changed' in item for item in drift)
