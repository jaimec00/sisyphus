# Copyright (c) 2026 Jaime C.
#
# Use of this source code is governed by an MIT-style
# license that can be found in the LICENSE file or at
# https://opensource.org/licenses/MIT.

"""Where the server's world lives: the flags, the env vars, and the default.

The default is load-bearing. ``python -m robot_mcp`` with no options must stay
in memory: ``test_stdio_transport.py`` compares a spawned server against a
fresh in-process ``MockBackend()``, and a server that quietly resumed a
previous run's world (or wrote into the developer's home directory) would break
that on its second run.
"""

from mcp_fixtures import INHERITED_ENV_TO_DROP
import pytest
from robot_backends import MockBackend
from robot_mcp import backend_from_options, parse_args, WORLD_SEED_ENV, WORLD_STATE_ENV
from robot_skills import Grasp, NavigateTo, Pose
from robot_world import FileWorldStore, read_document, WorldStoreError
from test_stdio_transport import server_parameters
from test_world_state_persists import persisted_server


def test_no_options_means_no_world_file(monkeypatch):
    """The documented command keeps today's in-memory behaviour."""
    monkeypatch.delenv(WORLD_STATE_ENV, raising=False)
    monkeypatch.delenv(WORLD_SEED_ENV, raising=False)

    args = parse_args([])

    assert args.world_state is None
    assert args.world_seed is None
    assert backend_from_options(args.world_state, args.world_seed) is None


def test_the_environment_can_supply_the_paths(monkeypatch, tmp_path):
    """A deployment configures the server without editing a command line."""
    monkeypatch.setenv(WORLD_STATE_ENV, str(tmp_path / 'live.json'))
    monkeypatch.setenv(WORLD_SEED_ENV, str(tmp_path / 'seed.json'))

    args = parse_args([])

    assert args.world_state == str(tmp_path / 'live.json')
    assert args.world_seed == str(tmp_path / 'seed.json')


def test_a_flag_beats_its_environment_variable(monkeypatch, tmp_path):
    """An explicit path on the command line wins, so a test can override a deploy."""
    monkeypatch.setenv(WORLD_STATE_ENV, str(tmp_path / 'from_env.json'))
    monkeypatch.delenv(WORLD_SEED_ENV, raising=False)

    args = parse_args(['--world-state', str(tmp_path / 'from_flag.json')])

    assert args.world_state == str(tmp_path / 'from_flag.json')


def test_a_seed_without_a_live_file_is_refused(monkeypatch, tmp_path, capsys):
    """A flag that would silently do nothing is an error, not a shrug."""
    monkeypatch.delenv(WORLD_STATE_ENV, raising=False)
    monkeypatch.delenv(WORLD_SEED_ENV, raising=False)

    with pytest.raises(SystemExit):
        parse_args(['--world-seed', str(tmp_path / 'seed.json')])

    assert '--world-seed needs --world-state' in capsys.readouterr().err


def test_the_options_build_a_file_backed_mock(tmp_path):
    """``--world-state`` puts the scene in that file, from the shipped seed."""
    live = tmp_path / 'live.json'

    backend = backend_from_options(str(live))

    assert isinstance(backend, MockBackend)
    assert isinstance(backend.store, FileWorldStore)
    assert backend.store.live_path == live
    assert backend.store.seed_path is None
    assert live.exists()
    assert backend.execute(NavigateTo('kitchen')).succeeded
    assert backend.execute(Grasp('mug_1')).succeeded
    assert read_document(live).find_object('mug_1').held_by is not None


def test_a_custom_seed_is_what_reset_restores(tmp_path):
    """``--world-seed`` replaces the shipped scene the ``reset`` tool goes back to."""
    from robot_world import WorldDocument, WorldObject, write_document

    seed = tmp_path / 'seed.json'
    write_document(seed, WorldDocument(
        locations={'dock': Pose.from_xyz(0.0, 0.0, 0.0)},
        start_location='dock',
        objects=(WorldObject('cube_1', 'cube', Pose.from_xyz(0.3, 0.0, 0.8)),),
    ))

    backend = backend_from_options(str(tmp_path / 'live.json'), str(seed))
    observation = backend.get_observation()

    assert observation.known_locations == ('dock',)
    assert [item.object_id for item in observation.objects] == ['cube_1']
    assert backend.execute(NavigateTo('kitchen')).succeeded is False
    assert backend.reset().find_object('cube_1').pose == Pose.from_xyz(0.3, 0.0, 0.8)


def test_a_spawned_server_never_inherits_the_worlds_environment(monkeypatch):
    """A developer's exported world file must not reach the suite's servers.

    ``python -m robot_mcp`` reads ``$ROBOT_WORLD_STATE``/``$ROBOT_WORLD_SEED``,
    and every spawned-server test copies the ambient environment.  Without this
    guard, exporting the documented variable would make those tests resume the
    developer's real world -- passing once against a clean file and failing
    every run after -- *and* write production state from a test run.
    """
    monkeypatch.setenv(WORLD_STATE_ENV, '/home/somebody/.local/state/robot/world.json')
    monkeypatch.setenv(WORLD_SEED_ENV, '/home/somebody/seed.json')
    monkeypatch.setenv('ROS_DOMAIN_ID', '7')

    env = server_parameters().env
    persisted = persisted_server('/tmp/live.json').env

    for name in INHERITED_ENV_TO_DROP:
        assert name not in env, name
        assert name not in persisted, name
    assert env['PYTHONPATH']
    # ...and the explicit flag is still how a persisted server is asked for.
    assert '--world-state' in persisted_server('/tmp/live.json').args


def test_seeding_from_the_live_file_is_refused(tmp_path):
    """One path for both options would silently turn the ``reset`` tool into a no-op."""
    live = tmp_path / 'world.json'
    args = parse_args(['--world-state', str(live), '--world-seed', str(live)])

    with pytest.raises(WorldStoreError, match='the same file'):
        backend_from_options(args.world_state, args.world_seed)
