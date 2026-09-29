# Copyright (c) 2026 Jaime C.
#
# Use of this source code is governed by an MIT-style
# license that can be found in the LICENSE file or at
# https://opensource.org/licenses/MIT.

"""Tests for the ``start_location`` mutator (PR3/issue #135, R3).

``set_start_location`` is the one scene-infrastructure write a *nav* skill
originates (the robot arrived at a named location), so its contract has the
same three parts as every other store mutator:

* a known location updates ``start_location``;
* an unknown one is refused loudly (``WorldStoreError``, the store's own
  refusal type) and leaves the scene untouched -- a store that committed
  ``start_location`` pointing nowhere would write a document its own parser
  refuses;
* setting the current value changes nothing, so an arrival back at the room the
  robot already stands in costs no disk write.

Persistence is checked at the file level too, like the object writes: the
mutation must outlive the store that made it.
"""

import pytest
from robot_skills import Pose
from robot_world import FileWorldStore, WorldStore, WorldStoreError

#: The two locations the shared small scene declares (conftest).
KNOWN_LOCATIONS = ('bench', 'dock')


def test_set_start_location_updates_the_property(document):
    """A known location becomes the start location (and nothing else changes)."""
    store = WorldStore(document)
    assert store.start_location == 'dock'

    store.set_start_location('bench')

    assert store.start_location == 'bench'
    # The rest of the scene is untouched: the locations map and the objects do
    # not move, and the column height is the robot's, not the room's.
    assert dict(store.locations()) == dict(document.locations)
    assert store.objects() == document.objects
    assert store.start_column_height == document.start_column_height


def test_set_start_location_same_value_is_a_no_op(document, monkeypatch):
    """Setting the current value commits nothing (no disk churn)."""
    commits = []
    monkeypatch.setattr(WorldStore, '_commit', lambda self: commits.append(1))
    store = WorldStore(document)
    before = store.document()
    commits.clear()

    store.set_start_location('dock')

    assert store.start_location == 'dock'
    assert store.document() == before
    assert commits == []
    assert store.pending_write is False


def test_set_start_location_refuses_an_unknown_location(document, monkeypatch):
    """An unknown name raises and changes nothing at all."""
    commits = []
    monkeypatch.setattr(WorldStore, '_commit', lambda self: commits.append(1))
    store = WorldStore(document)
    before = store.document()
    commits.clear()

    with pytest.raises(WorldStoreError, match="unknown location 'attic'"):
        store.set_start_location('attic')

    assert store.start_location == 'dock'
    assert store.document() == before
    assert commits == []


def test_set_start_location_lists_the_known_locations(document):
    """The refusal names the locations the caller could have meant."""
    store = WorldStore(document)
    with pytest.raises(WorldStoreError) as excinfo:
        store.set_start_location('attic')
    message = str(excinfo.value)
    assert 'known locations: bench, dock' in message


def test_set_start_location_rejects_a_blank_name(document):
    """A blank identifier is refused like any other malformed input."""
    store = WorldStore(document)
    with pytest.raises(ValueError):
        store.set_start_location('   ')
    assert store.start_location == 'dock'


def test_set_start_location_persists_through_a_re_open(tmp_path, seed_file):
    """The arrival survives the store that recorded it (atomic commit, D23)."""
    live = tmp_path / 'world.json'

    first = FileWorldStore(live, seed_path=seed_file)
    first.set_start_location('bench')

    second = FileWorldStore(live, seed_path=seed_file)

    assert second.start_location == 'bench'
    assert second.document() == first.document()


def test_set_start_location_can_round_trip_through_a_document(document):
    """The store's snapshot still parses: ``start_location`` stays a location."""
    store = WorldStore(document)
    store.set_start_location('bench')

    # document() is what FileWorldStore writes; parsing it back is the check
    # that the mutator cannot commit an unreadable scene.
    slimmed = store.document()
    assert slimmed.start_location == 'bench'
    assert slimmed.start_pose == Pose.from_xyz(1.0, 0.0, 0.0)
    assert tuple(sorted(slimmed.locations)) == KNOWN_LOCATIONS
