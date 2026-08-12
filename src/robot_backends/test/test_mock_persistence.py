# Copyright (c) 2026 Jaime C.
#
# Use of this source code is governed by an MIT-style
# license that can be found in the LICENSE file or at
# https://opensource.org/licenses/MIT.

"""Acceptance criteria: the Mock's world can live in a file and survive a restart.

These drive the backend the way a chore does -- navigate, grasp, place -- and
then ask the *file* what happened, or hand the file to a second backend and ask
that.  ``test_mock_skills.py`` already covers what each skill does; what is new
here is that it outlives the object that did it.
"""

import pytest
from robot_backends import MockBackend, MockWorld, ObjectSpec, RobotModel
from robot_skills import (
    ExtendColumn,
    Grasp,
    GripperState,
    NavigateTo,
    OpenGripper,
    Place,
    Pose,
    Side,
    SkillStatus,
)
from robot_world import (
    FileWorldStore,
    read_document,
    store as store_module,
    WorldDocument,
    WorldObject,
    WorldStore,
    write_document,
)


@pytest.fixture
def live_path(tmp_path):
    """Return a path for a live-state file that does not exist yet."""
    return tmp_path / 'world.json'


def carry_the_mug_to_the_table(backend: MockBackend) -> None:
    """Run the reference chore: fetch mug_1 from the kitchen, put it on the table."""
    for skill in (
        NavigateTo('kitchen'),
        Grasp('mug_1'),
        NavigateTo('table'),
        Place(Pose.from_xyz(0.35, 2.05, 0.75)),
    ):
        result = backend.execute(skill)
        assert result.status is SkillStatus.OK, (skill, result.reason)


def test_the_observation_reflects_what_is_in_the_live_file(live_path):
    """Criterion 1: state is loaded from, and written to, the JSON live file."""
    backend = MockBackend(store=FileWorldStore(live_path))

    # Loaded from: the file made at startup is what the observation reports.
    persisted = read_document(live_path)
    observed = backend.get_observation()
    assert {item.object_id for item in observed.objects} == {
        item.object_id for item in persisted.objects}
    assert observed.find_object('mug_1').pose == persisted.find_object('mug_1').pose
    assert observed.known_locations == tuple(sorted(persisted.locations))

    # ...and written to: a skill's effect is in the file, not just in memory.
    carry_the_mug_to_the_table(backend)
    on_the_table = read_document(live_path).find_object('mug_1')
    assert on_the_table.pose == Pose.from_xyz(0.35, 2.05, 0.75)
    assert on_the_table.held_by is None
    assert backend.get_observation().find_object('mug_1').pose == on_the_table.pose


def test_a_second_backend_over_the_same_file_sees_the_mutation(live_path):
    """Criterion 2, in-process form: a fresh backend resumes the persisted world."""
    first = MockBackend(store=FileWorldStore(live_path))
    carry_the_mug_to_the_table(first)

    second = MockBackend(store=FileWorldStore(live_path))

    assert second.get_observation().find_object('mug_1').pose == Pose.from_xyz(
        0.35, 2.05, 0.75)
    assert second.get_observation().objects == first.get_observation().objects


def test_a_restart_while_holding_something_releases_it_where_it_lies(live_path):
    """A power cycle: the object keeps its pose, the (empty) grippers win on held_by."""
    first = MockBackend(store=FileWorldStore(live_path))
    assert first.execute(NavigateTo('kitchen')).succeeded
    assert first.execute(Grasp('mug_1')).succeeded
    carried = first.get_observation().find_object('mug_1')
    assert carried.held_by is Side.LEFT
    assert read_document(live_path).find_object('mug_1').held_by is Side.LEFT

    second = MockBackend(store=FileWorldStore(live_path))
    resumed = second.get_observation()

    assert resumed.find_object('mug_1').pose == carried.pose
    assert resumed.find_object('mug_1').held_by is None
    assert resumed.held_objects() == ()
    for gripper in resumed.robot.grippers:
        assert gripper.state is GripperState.OPEN
        assert gripper.held_object_id is None
    # The robot itself re-homes: proprioception is not world state.
    assert resumed.robot.location == 'charger'
    assert resumed.robot.column_height == second.world.start_column_height
    # ...and the release is persisted, so the next restart is not a surprise.
    assert read_document(live_path).find_object('mug_1').held_by is None


def test_reset_restores_the_scene_from_the_seed_file(live_path, tmp_path):
    """Criterion 3: ``reset()`` comes from the read-only seed, not from memory."""
    seed_path = tmp_path / 'seed.json'
    seed_store = WorldStore()
    write_document(seed_path, seed_store.document())

    backend = MockBackend(store=FileWorldStore(live_path, seed_path=seed_path))
    carry_the_mug_to_the_table(backend)
    assert backend.execute(ExtendColumn(0.9)).succeeded
    moved = read_document(live_path).find_object('mug_1').pose

    observation = backend.reset()

    assert observation.find_object('mug_1').pose != moved
    assert observation.find_object('mug_1').pose == seed_store.find_object('mug_1').pose
    assert observation.robot.location == 'charger'
    assert observation.robot.column_height == 0.3
    # The restored scene reaches the file too, so a restart agrees with it.
    assert read_document(live_path) == seed_store.document()
    assert MockBackend(store=FileWorldStore(
        live_path, seed_path=seed_path)).get_observation().find_object(
            'mug_1').pose == seed_store.find_object('mug_1').pose


def test_one_skill_is_one_file_write(live_path, monkeypatch):
    """A skill that moves three objects still lands as a single atomic write."""
    backend = MockBackend(store=FileWorldStore(live_path))
    assert backend.execute(NavigateTo('kitchen')).succeeded
    assert backend.execute(Grasp('mug_1', side=Side.LEFT)).succeeded
    assert backend.execute(Grasp('plate_1', side=Side.RIGHT)).succeeded

    writes = []
    real_write = store_module.write_document

    def counting_write(path, document):
        writes.append(str(path))
        real_write(path, document)

    monkeypatch.setattr(store_module, 'write_document', counting_write)

    # Moves the base, and drags both held objects along with the grippers.
    result = backend.execute(NavigateTo('table'))

    assert result.succeeded
    assert len(writes) == 1, writes
    persisted = read_document(live_path)
    for object_id in ('mug_1', 'plate_1'):
        assert persisted.find_object(object_id).pose == result.observation.find_object(
            object_id).pose


def test_a_refused_skill_writes_nothing(live_path, monkeypatch):
    """A refusal leaves the world byte identical -- on disk as well as in memory."""
    backend = MockBackend(store=FileWorldStore(live_path))
    before = live_path.read_text(encoding='utf-8')

    monkeypatch.setattr(
        store_module,
        'write_document',
        lambda path, document: pytest.fail(f'a refused skill wrote {path}'),
    )

    refused = backend.execute(Grasp('mug_1'))  # out of reach from the charger

    assert refused.status is SkillStatus.FAILED
    assert live_path.read_text(encoding='utf-8') == before


def test_a_bare_backend_never_opens_a_file(tmp_path, monkeypatch):
    """Persistence is opt-in: today's ``MockBackend()`` is unchanged, and file-free."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        store_module,
        'write_document',
        lambda path, document: pytest.fail(f'a bare MockBackend wrote {path}'),
    )

    backend = MockBackend()
    carry_the_mug_to_the_table(backend)
    backend.reset()

    assert sorted(entry.name for entry in tmp_path.iterdir()) == []
    assert not isinstance(backend.store, FileWorldStore)


def test_a_store_and_a_world_split_the_scene_from_the_robot(live_path):
    """With both given, the store is the scene and ``world`` is only the body."""
    short_column = RobotModel(max_column_height=0.5)
    elsewhere = MockWorld(
        locations={'garage': Pose.from_xyz(9.0, 9.0, 0.0)},
        start_location='garage',
        objects=(ObjectSpec('crate_1', 'crate', Pose.from_xyz(9.0, 9.0, 0.5)),),
        robot=short_column,
    )
    backend = MockBackend(elsewhere, store=FileWorldStore(live_path))
    observation = backend.get_observation()

    # The scene came from the store, not from ``elsewhere``.
    assert observation.robot.location == 'charger'
    assert 'garage' not in observation.known_locations
    assert observation.find_object('crate_1') is None
    assert observation.find_object('mug_1') is not None
    # ...but the body is the short-column one ``elsewhere`` described, and it
    # is the body -- not the file -- that decides what the robot can do.
    assert backend.world.robot == short_column
    refused = backend.execute(ExtendColumn(0.9))
    assert refused.status is SkillStatus.FAILED
    assert 'outside the column range [0.00, 0.50] m' in refused.reason
    assert backend.execute(ExtendColumn(0.45)).succeeded


def test_a_scene_the_robot_cannot_come_up_in_is_refused(tmp_path):
    """A start height outside the column's travel fails loudly at construction."""
    seed_path = tmp_path / 'seed.json'
    write_document(seed_path, WorldDocument(
        locations={'dock': Pose.from_xyz(0.0, 0.0, 0.0)},
        start_location='dock',
        objects=(WorldObject('cube_1', 'cube', Pose.from_xyz(0.3, 0.0, 0.8)),),
        start_column_height=3.0,
    ))

    with pytest.raises(ValueError, match='outside the column range'):
        MockBackend(store=FileWorldStore(tmp_path / 'world.json', seed_path=seed_path))


def test_dropping_a_held_object_persists_where_it_landed(live_path):
    """``open_gripper`` mid-carry is a mutation like any other: it reaches the file."""
    backend = MockBackend(store=FileWorldStore(live_path))
    assert backend.execute(NavigateTo('kitchen')).succeeded
    assert backend.execute(Grasp('mug_1')).succeeded
    dropped = backend.execute(OpenGripper(Side.LEFT))

    assert dropped.succeeded
    persisted = read_document(live_path).find_object('mug_1')
    assert persisted.held_by is None
    assert persisted.pose == dropped.observation.find_object('mug_1').pose
