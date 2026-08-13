# Copyright (c) 2026 Jaime C.
#
# Use of this source code is governed by an MIT-style
# license that can be found in the LICENSE file or at
# https://opensource.org/licenses/MIT.

"""Tests for the in-memory store surface: queries, mutations, reset, batches."""

import pytest
from robot_skills import Pose, Side
from robot_world import WorldDocument, WorldObject, WorldStore, WorldStoreError


def test_the_store_answers_the_queries_the_brain_needs(document):
    """Locations, objects and start parameters are all readable directly."""
    store = WorldStore(document)

    assert sorted(store.locations()) == ['bench', 'dock']
    assert store.location('bench') == Pose.from_xyz(1.0, 0.0, 0.0)
    assert store.location('attic') is None
    assert [item.object_id for item in store.objects()] == ['cube_1', 'anvil_1']
    assert store.find_object('cube_1').label == 'cube'
    assert store.find_object('nope') is None
    assert store.start_location == 'dock'
    assert store.start_column_height == 0.4


def test_the_map_cannot_be_mutated_through_a_query(document):
    """``locations()`` hands out a read-only view, not the store's own dict."""
    store = WorldStore(document)
    with pytest.raises(TypeError):
        store.locations()['attic'] = Pose()
    assert 'attic' not in store.locations()


def test_moving_and_holding_an_object(document):
    """Pose and held-by are updated in place, leaving everything else alone."""
    store = WorldStore(document)
    where = Pose.from_xyz(0.5, 0.5, 0.9)

    store.update_object_pose('cube_1', where)
    store.set_held_by('cube_1', Side.RIGHT)

    moved = store.find_object('cube_1')
    assert moved.pose == where
    assert moved.held_by is Side.RIGHT
    assert moved.label == 'cube' and moved.graspable is True
    assert store.find_object('anvil_1') == document.find_object('anvil_1')
    # ...and order is stable, so the file does not churn on every mutation.
    assert [item.object_id for item in store.objects()] == ['cube_1', 'anvil_1']

    store.set_held_by('cube_1', None)
    assert store.find_object('cube_1').held_by is None
    assert store.find_object('cube_1').pose == where


def test_a_gripper_cannot_be_given_a_second_object_to_hold(document, monkeypatch):
    """The second claim on one gripper is refused, and changes nothing at all."""
    commits = []
    monkeypatch.setattr(WorldStore, '_commit', lambda self: commits.append(1))
    store = WorldStore(document)
    store.set_held_by('cube_1', Side.LEFT)
    before = store.document()
    commits.clear()

    with pytest.raises(WorldStoreError, match='left gripper: it already holds'):
        store.set_held_by('anvil_1', Side.LEFT)

    # Check-then-mutate: the refused call left the registry byte-identical,
    # dirtied nothing, and therefore cost no write.
    assert store.document() == before
    assert store.find_object('cube_1').held_by is Side.LEFT
    assert store.find_object('anvil_1').held_by is None
    assert commits == []
    assert store.pending_write is False


def test_a_refused_hold_inside_a_batch_leaves_the_batch_intact(document, monkeypatch):
    """A refusal mid-batch corrupts neither the scene nor the batch book-keeping."""
    commits = []
    monkeypatch.setattr(WorldStore, '_commit', lambda self: commits.append(1))
    store = WorldStore(document)

    # A refusal is immediate inside a batch too: the in-memory scene is exactly
    # what a direct reader sees, so it may not go inconsistent until commit.
    with store.batch():
        store.set_held_by('cube_1', Side.RIGHT)
        with pytest.raises(WorldStoreError, match='right gripper: it already holds'):
            store.set_held_by('anvil_1', Side.RIGHT)
        assert store.find_object('anvil_1').held_by is None
        assert commits == []
    assert commits == [1]
    assert store.pending_write is False

    # The batch's depth came back to zero, so the next mutation commits at once.
    store.set_held_by('cube_1', None)
    assert commits == [1, 1]

    # A batch whose only call is refused leaves nothing pending to commit.
    store.set_held_by('cube_1', Side.LEFT)
    commits.clear()
    with store.batch():
        with pytest.raises(WorldStoreError, match='it already holds'):
            store.set_held_by('anvil_1', Side.LEFT)
    assert commits == []
    assert store.pending_write is False


def test_a_new_object_cannot_arrive_in_a_full_gripper(document):
    """``add_object`` is the other way a hold could collide, and is checked too."""
    store = WorldStore(document)
    store.set_held_by('cube_1', Side.RIGHT)

    with pytest.raises(WorldStoreError, match='right gripper: it already holds'):
        store.add_object(WorldObject('tray_1', 'tray', Pose(), held_by=Side.RIGHT))
    assert store.find_object('tray_1') is None
    assert [item.object_id for item in store.objects()] == ['cube_1', 'anvil_1']

    # The free gripper is another matter: that object does join the scene.
    store.add_object(WorldObject('tray_1', 'tray', Pose(), held_by=Side.LEFT))
    assert store.find_object('tray_1').held_by is Side.LEFT


def test_a_hold_changes_hands_by_clearing_it_first(document):
    """Releasing is never refused; clear-then-set is how an object moves gripper."""
    store = WorldStore(document)
    store.set_held_by('cube_1', Side.LEFT)

    store.set_held_by('cube_1', None)
    store.set_held_by('anvil_1', Side.LEFT)
    assert store.find_object('anvil_1').held_by is Side.LEFT

    # Both grippers full at once is legal -- one object each.
    store.set_held_by('cube_1', Side.RIGHT)
    assert [item.held_by for item in store.objects()] == [Side.RIGHT, Side.LEFT]

    # Re-asserting a hold the object already has stays a no-op, not a conflict.
    store.set_held_by('cube_1', Side.RIGHT)
    store.set_held_by('anvil_1', None)
    store.set_held_by('anvil_1', None)
    assert store.find_object('anvil_1').held_by is None


def test_a_conflicting_scene_cannot_reach_a_store_at_all(document):
    """Loading needs no check of its own: a document cannot describe a conflict."""
    with pytest.raises(ValueError, match='held by the same gripper'):
        WorldDocument(
            locations=dict(document.locations),
            start_location=document.start_location,
            objects=(
                WorldObject('cube_1', 'cube', Pose(), held_by=Side.LEFT),
                WorldObject('anvil_1', 'anvil', Pose(), held_by=Side.LEFT),
            ),
        )

    # A *legal* held scene loads, and survives a round trip through reset().
    carried = WorldDocument(
        locations=dict(document.locations),
        start_location=document.start_location,
        objects=(
            WorldObject('cube_1', 'cube', Pose(), held_by=Side.LEFT),
            WorldObject('anvil_1', 'anvil', Pose(), held_by=Side.RIGHT),
        ),
    )
    store = WorldStore(carried)
    assert store.find_object('cube_1').held_by is Side.LEFT
    store.set_held_by('cube_1', None)
    store.reset()
    assert store.document() == carried


def test_objects_can_join_and_leave_the_scene(document):
    """The registry grows and shrinks; a duplicate id is refused."""
    store = WorldStore(document)
    store.add_object(WorldObject('tray_1', 'tray', Pose.from_xyz(1.0, 0.0, 0.7)))
    assert store.find_object('tray_1').label == 'tray'

    with pytest.raises(WorldStoreError, match='already holds an object'):
        store.add_object(WorldObject('tray_1', 'tray', Pose()))

    store.remove_object('tray_1')
    assert store.find_object('tray_1') is None
    with pytest.raises(WorldStoreError, match='no object'):
        store.remove_object('tray_1')


def test_mutating_an_unknown_object_is_refused(document):
    """A typo names itself instead of silently registering a ghost object."""
    store = WorldStore(document)
    with pytest.raises(WorldStoreError, match='no object'):
        store.update_object_pose('ghost_1', Pose())
    with pytest.raises(WorldStoreError, match='no object'):
        store.set_held_by('ghost_1', Side.LEFT)
    assert [item.object_id for item in store.objects()] == ['cube_1', 'anvil_1']
    with pytest.raises(TypeError, match='must be a Pose'):
        store.update_object_pose('cube_1', (1.0, 2.0, 3.0))


def test_reset_restores_the_seed_scene(document):
    """Every mutation since construction is discarded, including add/remove."""
    store = WorldStore(document)
    store.update_object_pose('cube_1', Pose.from_xyz(9.0, 9.0, 9.0))
    store.set_held_by('cube_1', Side.LEFT)
    store.remove_object('anvil_1')
    store.add_object(WorldObject('tray_1', 'tray', Pose()))

    store.reset()

    assert store.document() == document
    assert store.find_object('tray_1') is None
    assert store.find_object('cube_1').pose == Pose.from_xyz(1.0, 0.1, 0.8)


def test_the_seed_is_not_disturbed_by_anything_the_store_does(document):
    """``seed_document()`` keeps answering ground truth, mutation after mutation."""
    store = WorldStore(document)
    store.update_object_pose('cube_1', Pose.from_xyz(9.0, 9.0, 9.0))
    store.set_held_by('cube_1', Side.LEFT)
    store.remove_object('anvil_1')
    store.add_object(WorldObject('tray_1', 'tray', Pose()))

    assert store.seed_document() == document
    store.reset()
    assert store.document() == document
    assert store.seed_document() == document


def test_a_store_can_be_told_its_seed_separately_from_its_scene(document):
    """Coming up on a drifted scene is not the same as being seeded from it.

    The distinction is what a :class:`FileWorldStore` reopening a live file
    needs: the scene it loads is *not* the scene ``reset()`` must restore.
    """
    drifted = WorldStore(document)
    drifted.update_object_pose('cube_1', Pose.from_xyz(9.0, 9.0, 9.0))
    scene = drifted.document()

    store = WorldStore(scene, seed=document)

    assert store.document() == scene
    assert store.seed_document() == document
    store.reset()
    assert store.document() == document

    # ...and the new argument is type-checked where it is written, like the other.
    with pytest.raises(TypeError, match='seed must be a WorldDocument'):
        WorldStore(document, seed={'locations': {}})


def test_the_document_is_a_snapshot_not_a_live_view(document):
    """``document()`` returns the current scene, frozen at the moment it is asked."""
    store = WorldStore(document)
    before = store.document()
    store.update_object_pose('cube_1', Pose.from_xyz(0.0, 0.0, 1.0))

    assert before == document
    assert store.document() != before
    assert store.document().find_object('cube_1').pose == Pose.from_xyz(0.0, 0.0, 1.0)


def test_the_default_store_holds_the_shipped_scene():
    """``WorldStore()`` with no arguments is the demo apartment, from the seed."""
    store = WorldStore()
    assert store.start_location == 'charger'
    assert 'kitchen' in store.locations()
    assert store.find_object('mug_1').graspable is True


def test_a_store_refuses_something_that_is_not_a_document():
    """The one constructor argument is type-checked where it is written."""
    with pytest.raises(TypeError, match='must be a WorldDocument'):
        WorldStore({'locations': {}})


def test_batches_group_mutations_into_one_commit(document, monkeypatch):
    """A batch commits once; outside one, every mutation commits on its own."""
    commits = []
    monkeypatch.setattr(
        WorldStore, '_commit', lambda self: commits.append(len(self.objects())))
    store = WorldStore(document)

    store.update_object_pose('cube_1', Pose.from_xyz(0.1, 0.0, 0.8))
    store.set_held_by('cube_1', Side.LEFT)
    assert len(commits) == 2

    commits.clear()
    with store.batch():
        store.update_object_pose('cube_1', Pose.from_xyz(0.2, 0.0, 0.8))
        store.set_held_by('cube_1', None)
        store.add_object(WorldObject('tray_1', 'tray', Pose()))
        assert commits == []
    assert len(commits) == 1


def test_nested_batches_commit_once_at_the_outermost_exit(document, monkeypatch):
    """Only the outer batch flushes, so a helper can batch without double-writing."""
    commits = []
    monkeypatch.setattr(WorldStore, '_commit', lambda self: commits.append(1))
    store = WorldStore(document)

    with store.batch():
        with store.batch():
            store.set_held_by('cube_1', Side.LEFT)
        assert commits == []
    assert commits == [1]


def test_a_batch_left_by_an_exception_still_commits(document, monkeypatch):
    """The file must not silently disagree with what the caller can already read."""
    commits = []
    monkeypatch.setattr(WorldStore, '_commit', lambda self: commits.append(1))
    store = WorldStore(document)

    with pytest.raises(RuntimeError):
        with store.batch():
            store.set_held_by('cube_1', Side.LEFT)
            raise RuntimeError('skill blew up half way')

    assert commits == [1]
    assert store.find_object('cube_1').held_by is Side.LEFT


def test_a_no_op_mutation_does_not_commit(document, monkeypatch):
    """Rewriting the same value is not a change, so it costs no disk write."""
    commits = []
    monkeypatch.setattr(WorldStore, '_commit', lambda self: commits.append(1))
    store = WorldStore(document)

    store.update_object_pose('cube_1', store.find_object('cube_1').pose)
    store.set_held_by('cube_1', None)

    assert commits == []
