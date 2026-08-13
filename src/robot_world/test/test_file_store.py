# Copyright (c) 2026 Jaime C.
#
# Use of this source code is governed by an MIT-style
# license that can be found in the LICENSE file or at
# https://opensource.org/licenses/MIT.

"""Tests for the file-backed store: it persists, it reseeds, it fails loudly.

The point of the whole feature is here: a mutation must outlive the object that
made it, and ``reset()`` must come from the *seed file* rather than from
anything the running process happens to remember.
"""

import json

import pytest
from robot_skills import Pose, Side
from robot_world import (
    FileWorldStore,
    read_document,
    store as store_module,
    WorldObject,
    WorldStore,
    WorldStoreError,
    write_document,
)


def test_a_missing_live_file_is_created_from_the_seed(tmp_path, seed_file, document):
    """First run on a fresh checkout just works, and leaves a readable file."""
    live = tmp_path / 'state' / 'world.json'
    live.parent.mkdir()

    store = FileWorldStore(live, seed_path=seed_file)

    assert live.exists()
    assert store.document() == document
    assert read_document(live) == document
    assert store.live_path == live
    assert str(store.seed_path) == seed_file


def test_a_mutation_outlives_the_store_that_made_it(tmp_path, seed_file):
    """A second store over the same file sees what the first one wrote."""
    live = tmp_path / 'world.json'
    where = Pose.from_xyz(0.25, 2.05, 0.75)

    first = FileWorldStore(live, seed_path=seed_file)
    first.update_object_pose('cube_1', where)
    first.set_held_by('cube_1', Side.LEFT)
    first.add_object(WorldObject('tray_1', 'tray', Pose.from_xyz(1.0, 0.0, 0.7)))
    first.remove_object('anvil_1')

    second = FileWorldStore(live, seed_path=seed_file)

    assert second.find_object('cube_1').pose == where
    assert second.find_object('cube_1').held_by is Side.LEFT
    assert second.find_object('tray_1').label == 'tray'
    assert second.find_object('anvil_1') is None
    assert second.document() == first.document()


def test_reset_restores_from_the_seed_file_not_from_memory(tmp_path, seed_file, document):
    """Ground truth is the file on disk: rewrite it and ``reset()`` follows it.

    This is what "the seed is a *read-only file*, not a re-serialized Python
    literal" has to mean -- otherwise ``reset()`` would restore a snapshot the
    process took at startup and the seed file would be decoration.
    """
    live = tmp_path / 'world.json'
    store = FileWorldStore(live, seed_path=seed_file)
    store.update_object_pose('cube_1', Pose.from_xyz(9.0, 9.0, 9.0))

    reseeded = WorldStore(document)
    reseeded.update_object_pose('cube_1', Pose.from_xyz(0.4, 0.4, 0.4))
    write_document(seed_file, reseeded.document())

    store.reset()

    assert store.find_object('cube_1').pose == Pose.from_xyz(0.4, 0.4, 0.4)
    assert read_document(live).find_object('cube_1').pose == Pose.from_xyz(0.4, 0.4, 0.4)


def test_a_reopened_store_resets_to_the_seed_not_to_the_scene_it_opened(
    tmp_path, seed_file, document,
):
    """A store that comes up on a drifted world still knows its ground truth.

    The second store is constructed from the *live* file, which by then differs
    from the seed in every way a chore can change it.  Its seed is still the
    seed -- including the inherited ``_seed`` attribute, read here through the
    un-overridden base method, so the day someone collapses
    ``FileWorldStore.seed_document()`` into ``return self._seed`` they do not
    silently get "whatever the live file said at startup" back.
    """
    live = tmp_path / 'world.json'
    first = FileWorldStore(live, seed_path=seed_file)
    first.update_object_pose('cube_1', Pose.from_xyz(9.0, 9.0, 9.0))
    first.set_held_by('cube_1', Side.LEFT)
    first.remove_object('anvil_1')

    second = FileWorldStore(live, seed_path=seed_file)
    drifted = second.document()
    assert drifted != document

    assert second.seed_document() == document
    assert WorldStore.seed_document(second) == document
    assert WorldStore.seed_document(second) != drifted

    second.reset()

    assert second.document() == document
    assert second.document() != drifted
    assert read_document(live) == document


def test_mutating_the_working_scene_never_changes_what_reset_restores(
    tmp_path, seed_file, document,
):
    """The seed is untouchable through the store: only the live file moves."""
    live = tmp_path / 'world.json'
    store = FileWorldStore(live, seed_path=seed_file)

    store.update_object_pose('cube_1', Pose.from_xyz(9.0, 9.0, 9.0))
    store.set_held_by('cube_1', Side.RIGHT)
    store.add_object(WorldObject('tray_1', 'tray', Pose.from_xyz(1.0, 0.0, 0.7)))
    store.remove_object('anvil_1')

    assert store.seed_document() == document
    assert WorldStore.seed_document(store) == document
    assert read_document(seed_file) == document

    # ...and a store opened on the drifted live file is seeded from the seed
    # too, not from what it happened to load (pinned here as well as in
    # ``test_a_reopened_store_resets_to_the_seed_not_to_the_scene_it_opened``,
    # so losing that guarantee takes editing two tests, not deleting one line).
    reopened = FileWorldStore(live, seed_path=seed_file)
    assert reopened.document() != document
    assert WorldStore.seed_document(reopened) == document

    store.reset()
    assert store.document() == document


def test_reset_rewrites_the_live_file(tmp_path, seed_file, document):
    """The restored scene reaches disk, so the next process sees the reset too."""
    live = tmp_path / 'world.json'
    store = FileWorldStore(live, seed_path=seed_file)
    store.set_held_by('cube_1', Side.RIGHT)
    store.remove_object('anvil_1')

    store.reset()

    assert read_document(live) == document
    assert FileWorldStore(live, seed_path=seed_file).document() == document


def test_a_corrupt_live_file_is_never_silently_repaired(tmp_path, seed_file):
    """The evidence survives: we refuse to open it, and we do not overwrite it."""
    live = tmp_path / 'world.json'
    live.write_text('{"start_location": "dock", ', encoding='utf-8')

    with pytest.raises(WorldStoreError, match='invalid JSON'):
        FileWorldStore(live, seed_path=seed_file)

    assert live.read_text(encoding='utf-8') == '{"start_location": "dock", '


def test_a_schema_violating_live_file_is_refused(tmp_path, seed_file, document):
    """Valid JSON that is not a valid world is just as loud, and names the key."""
    live = tmp_path / 'world.json'
    data = document.to_dict()
    data['objects'][0]['object_id'] = ''
    live.write_text(json.dumps(data), encoding='utf-8')

    with pytest.raises(WorldStoreError, match='WorldObject'):
        FileWorldStore(live, seed_path=seed_file)

    live.write_text('[]', encoding='utf-8')
    with pytest.raises(WorldStoreError, match='expected a JSON object'):
        FileWorldStore(live, seed_path=seed_file)


def test_a_live_file_holding_one_object_in_two_grippers_is_refused(
    tmp_path, seed_file, document,
):
    """An impossible scene on disk is refused at load, like any other corruption.

    Recovering it is a hand repair (or deleting the file), not a silent
    rewrite: overwriting an inconsistent world with the seed would destroy the
    evidence of whatever produced it.
    """
    live = tmp_path / 'world.json'
    data = document.to_dict()
    data['objects'][0]['held_by'] = 'right'
    data['objects'][1]['held_by'] = 'right'
    text = json.dumps(data)
    live.write_text(text, encoding='utf-8')

    with pytest.raises(WorldStoreError, match='held by the same gripper: right'):
        FileWorldStore(live, seed_path=seed_file)

    assert live.read_text(encoding='utf-8') == text


def test_a_missing_or_corrupt_seed_is_a_hard_error(tmp_path):
    """A broken install / misconfiguration fails at startup, not mid-chore."""
    live = tmp_path / 'world.json'
    with pytest.raises(WorldStoreError, match='cannot read world file'):
        FileWorldStore(live, seed_path=tmp_path / 'nowhere.json')
    assert not live.exists()

    broken = tmp_path / 'seed.json'
    broken.write_text('not json at all', encoding='utf-8')
    with pytest.raises(WorldStoreError, match='invalid JSON'):
        FileWorldStore(live, seed_path=broken)


def test_the_shipped_seed_is_the_default(tmp_path):
    """With no seed path, a fresh live file is the demo apartment."""
    live = tmp_path / 'world.json'
    store = FileWorldStore(live)

    assert store.seed_path is None
    assert store.start_location == 'charger'
    assert store.find_object('mug_1').graspable is True
    assert read_document(live).find_object('sofa_1').graspable is False


def test_one_batch_is_one_file_write(tmp_path, seed_file, monkeypatch):
    """A caller grouping mutations pays for one atomic transition, not four."""
    live = tmp_path / 'world.json'
    store = FileWorldStore(live, seed_path=seed_file)

    writes = []
    real_write = store_module.write_document
    monkeypatch.setattr(
        store_module,
        'write_document',
        lambda path, document: (writes.append(str(path)), real_write(path, document))[1],
    )

    with store.batch():
        store.update_object_pose('cube_1', Pose.from_xyz(0.3, 0.0, 0.8))
        store.set_held_by('cube_1', Side.LEFT)
        store.update_object_pose('anvil_1', Pose.from_xyz(0.9, 0.0, 0.8))

    assert writes == [str(live)]
    persisted = read_document(live)
    assert persisted.find_object('cube_1').held_by is Side.LEFT
    assert persisted.find_object('anvil_1').pose == Pose.from_xyz(0.9, 0.0, 0.8)


def test_the_live_file_stays_human_readable(tmp_path, seed_file):
    """Indented, newline-terminated JSON: debuggable, diffable, still machine-written."""
    live = tmp_path / 'world.json'
    FileWorldStore(live, seed_path=seed_file)

    text = live.read_text(encoding='utf-8')
    assert text.endswith('}\n')
    assert '\n  "start_location": "dock"' in text


def test_a_failed_commit_leaves_the_store_dirty_and_the_next_write_repairs_it(
    tmp_path, seed_file, monkeypatch,
):
    """A full disk must not make the store *believe* it is in sync.

    Clearing the dirty flag before the write succeeds would strand the file one
    mutation behind for ever: the next mutation would see a clean store, and
    nothing would ever retry.  Because a commit writes the whole document, an
    honest flag makes the very next mutation repair the file.
    """
    live = tmp_path / 'world.json'
    store = FileWorldStore(live, seed_path=seed_file)
    failures = []

    real_write = store_module.write_document

    def write_once_broken(path, document):
        if not failures:
            failures.append(path)
            raise WorldStoreError('simulated full disk')
        real_write(path, document)

    monkeypatch.setattr(store_module, 'write_document', write_once_broken)

    with pytest.raises(WorldStoreError, match='full disk'):
        store.update_object_pose('cube_1', Pose.from_xyz(0.5, 0.0, 0.8))

    assert failures, 'the write was never attempted'
    assert store.pending_write is True
    # The lost mutation is only in memory so far...
    assert read_document(live).find_object('cube_1').pose != Pose.from_xyz(0.5, 0.0, 0.8)

    # ...and the next mutation of any kind writes the whole document, so both
    # changes land together.
    store.set_held_by('cube_1', Side.RIGHT)

    persisted = read_document(live)
    assert persisted.find_object('cube_1').pose == Pose.from_xyz(0.5, 0.0, 0.8)
    assert persisted.find_object('cube_1').held_by is Side.RIGHT
    assert store.pending_write is False


def test_a_seed_that_is_the_live_file_is_refused(tmp_path, document):
    """Seeding from the file you write turns ``reset()`` into a silent no-op."""
    live = tmp_path / 'world.json'
    write_document(live, document)

    with pytest.raises(WorldStoreError, match='the same file'):
        FileWorldStore(live, seed_path=live)

    # ...including when the two paths merely resolve to the same file.
    nested = tmp_path / 'state'
    nested.mkdir()
    with pytest.raises(WorldStoreError, match='the same file'):
        FileWorldStore(live, seed_path=nested / '..' / 'world.json')

    link = tmp_path / 'seed_link.json'
    link.symlink_to(live)
    with pytest.raises(WorldStoreError, match='the same file'):
        FileWorldStore(live, seed_path=link)

    # A genuinely separate seed is of course fine.
    separate = tmp_path / 'seed.json'
    write_document(separate, document)
    assert FileWorldStore(live, seed_path=separate).document() == document
