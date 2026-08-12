# Copyright (c) 2026 Jaime C.
#
# Use of this source code is governed by an MIT-style
# license that can be found in the LICENSE file or at
# https://opensource.org/licenses/MIT.

"""Tests for the world document: round trips, strict parsing, its own version."""

from dataclasses import FrozenInstanceError
import json

import pytest
from robot_skills import Pose, SCHEMA_VERSION_KEY, SerializationError, Side
from robot_world import (
    WORLD_SCHEMA_VERSION,
    WORLD_SCHEMA_VERSION_KEY,
    WorldDocument,
    WorldObject,
)


def round_trip(value):
    """Assert the JsonSerializable contract on ``value`` and return its dict."""
    data = value.to_dict()
    assert type(value).from_dict(data) == value
    assert json.loads(json.dumps(data)) == data
    return data


def test_object_round_trips_through_json(document):
    """``from_dict(to_dict(x)) == x`` and the dict survives a JSON trip."""
    held = WorldObject(
        'mug_1', 'mug', Pose.from_xyz(0.1, 0.2, 0.3), graspable=True, held_by=Side.LEFT)
    data = round_trip(held)
    assert data['held_by'] == 'left'
    assert data['graspable'] is True

    free = WorldObject('sofa_1', 'sofa', Pose.from_xyz(-2.0, 1.6, 0.4), graspable=False)
    assert round_trip(free)['held_by'] is None


def test_document_round_trips_through_json(document):
    """A whole scene survives to_dict -> json -> from_dict unchanged."""
    data = round_trip(document)
    assert data['start_location'] == 'dock'
    assert data['start_column_height'] == 0.4
    assert set(data['locations']) == {'dock', 'bench'}
    assert [item['object_id'] for item in data['objects']] == ['cube_1', 'anvil_1']

    # ...including a mutated copy carrying a held object.
    carried = WorldDocument(
        locations=document.locations,
        start_location=document.start_location,
        objects=(
            WorldObject('cube_1', 'cube', Pose.from_xyz(1.0, 0.1, 0.8), held_by=Side.RIGHT),
        ),
        start_column_height=document.start_column_height,
    )
    assert round_trip(carried)['objects'][0]['held_by'] == 'right'


def test_document_preserves_object_order(document):
    """Objects come back in file order, so a curated seed keeps its grouping."""
    reparsed = WorldDocument.from_dict(document.to_dict())
    assert [item.object_id for item in reparsed.objects] == ['cube_1', 'anvil_1']


def test_the_world_stamp_is_its_own_counter(document):
    """The file format versions independently of the skill API (D18 vs D23)."""
    data = document.to_dict()
    assert data[WORLD_SCHEMA_VERSION_KEY] == WORLD_SCHEMA_VERSION
    assert WORLD_SCHEMA_VERSION_KEY != SCHEMA_VERSION_KEY
    assert SCHEMA_VERSION_KEY not in data
    for item in data['objects']:
        assert SCHEMA_VERSION_KEY not in item


def test_a_foreign_world_version_is_refused(document):
    """A file from another format version is a loud error, not a guess."""
    data = document.to_dict()
    data[WORLD_SCHEMA_VERSION_KEY] = WORLD_SCHEMA_VERSION + 1
    with pytest.raises(SerializationError, match='unsupported world schema version'):
        WorldDocument.from_dict(data)

    data[WORLD_SCHEMA_VERSION_KEY] = 'one'
    with pytest.raises(SerializationError, match='expected an integer'):
        WorldDocument.from_dict(data)


def test_an_absent_world_stamp_reads_as_the_current_version(document):
    """An unstamped hand-written file still parses (added fields stay non-breaking)."""
    data = document.to_dict()
    del data[WORLD_SCHEMA_VERSION_KEY]
    assert WorldDocument.from_dict(data) == document


def test_unknown_keys_are_refused(document):
    """A typo in a hand-edited world file fails loudly instead of being ignored."""
    data = document.to_dict()
    data['start_locations'] = 'dock'
    with pytest.raises(SerializationError, match='unknown key'):
        WorldDocument.from_dict(data)

    data = document.to_dict()
    data['objects'][0]['pose_'] = data['objects'][0]['pose']
    with pytest.raises(SerializationError, match='unknown key'):
        WorldDocument.from_dict(data)


def test_missing_and_mistyped_keys_are_refused(document):
    """Required keys are required; a wrong type names the field it came from."""
    data = document.to_dict()
    del data['locations']
    with pytest.raises(SerializationError, match='missing required key'):
        WorldDocument.from_dict(data)

    data = document.to_dict()
    data['start_column_height'] = 'high'
    with pytest.raises(SerializationError, match='start_column_height'):
        WorldDocument.from_dict(data)

    data = document.to_dict()
    data['objects'] = {'cube_1': {}}
    with pytest.raises(SerializationError, match='expected a list'):
        WorldDocument.from_dict(data)

    data = document.to_dict()
    data['objects'][0]['held_by'] = 'middle'
    with pytest.raises(SerializationError, match='not a valid Side'):
        WorldDocument.from_dict(data)


def test_scene_invariants_are_enforced_at_the_parse_boundary(document):
    """A document that parses but describes an impossible scene is refused."""
    data = document.to_dict()
    data['start_location'] = 'attic'
    with pytest.raises(SerializationError, match='not one of the locations'):
        WorldDocument.from_dict(data)

    data = document.to_dict()
    data['objects'].append(data['objects'][0])
    with pytest.raises(SerializationError, match='duplicate object_id'):
        WorldDocument.from_dict(data)

    data = document.to_dict()
    data['locations'] = {}
    with pytest.raises(SerializationError, match='must not be empty'):
        WorldDocument.from_dict(data)


def test_document_is_immutable_and_defensively_copied():
    """Nothing can rewrite a document behind a store's back."""
    locations = {'dock': Pose.from_xyz(0.0, 0.0, 0.0)}
    document = WorldDocument(locations=locations, start_location='dock')

    locations['attic'] = Pose.from_xyz(0.0, 0.0, 3.0)
    assert 'attic' not in document.locations
    with pytest.raises(TypeError):
        document.locations['attic'] = Pose()
    with pytest.raises(FrozenInstanceError):
        document.start_location = 'attic'
    with pytest.raises(FrozenInstanceError):
        WorldObject('cube_1', 'cube', Pose()).pose = Pose.from_xyz(1.0, 0.0, 0.0)


def test_object_validation():
    """Objects need a real id, label, pose and (optional) holder."""
    with pytest.raises(ValueError):
        WorldObject('', 'cube', Pose())
    with pytest.raises(TypeError, match='must be a Pose'):
        WorldObject('cube_1', 'cube', (0.0, 0.0, 0.0))
    with pytest.raises(TypeError, match='must be a bool'):
        WorldObject('cube_1', 'cube', Pose(), graspable='yes')
    with pytest.raises(ValueError):
        WorldObject('cube_1', 'cube', Pose(), held_by='middle')
    assert WorldObject('cube_1', 'cube', Pose(), held_by='left').held_by is Side.LEFT


def test_document_lookup_and_start_pose(document):
    """A document answers "where is X" and "where do I come up" directly."""
    assert document.find_object('cube_1').label == 'cube'
    assert document.find_object('nope') is None
    assert document.start_pose == document.locations['dock']
