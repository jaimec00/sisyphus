# Copyright (c) 2026 Jaime C.
#
# Use of this source code is governed by an MIT-style
# license that can be found in the LICENSE file or at
# https://opensource.org/licenses/MIT.

"""Acceptance criterion: a crash mid-write cannot corrupt the live-state file.

The failures are injected where they actually hurt -- *between* writing the
temp file and renaming it over the target, and *while* writing it -- because
those are the windows in which a naive ``open(path, 'w')`` has already
truncated the real file and left a half document behind.
"""

import os

import pytest
from robot_skills import Pose, Side
from robot_world import (
    FileWorldStore,
    read_document,
    storage,
    store as store_module,
    WorldStore,
    WorldStoreError,
    write_document,
)


def files_in(directory) -> list[str]:
    """Return every entry in ``directory``, sorted -- temp files included."""
    return sorted(entry.name for entry in os.scandir(directory))


class FullDisk:
    """A file object that closes cleanly but refuses to write, as a full disk does."""

    def __init__(self, stream) -> None:
        self._stream = stream

    def __enter__(self) -> 'FullDisk':
        """Enter the ``with`` block, standing in for the real stream."""
        return self

    def __exit__(self, *exception) -> bool | None:
        """Close the underlying stream, so the temp file's fd is not leaked."""
        return self._stream.__exit__(*exception)

    def write(self, data: str) -> int:
        """Refuse the write the way a full filesystem does."""
        raise OSError(28, 'No space left on device')


def test_a_crash_between_temp_and_rename_leaves_the_file_intact(
    tmp_path, seed_file, document, monkeypatch,
):
    """The previous document is still there, still parseable, and unchanged."""
    live = tmp_path / 'world.json'
    store = FileWorldStore(live, seed_path=seed_file)
    before = live.read_text(encoding='utf-8')

    def crash(source, target):
        raise OSError(5, 'simulated crash between write and rename')

    monkeypatch.setattr(storage.os, 'replace', crash)

    with pytest.raises(WorldStoreError, match='cannot write world file'):
        store.update_object_pose('cube_1', Pose.from_xyz(9.0, 9.0, 9.0))

    assert live.read_text(encoding='utf-8') == before
    assert read_document(live) == document
    assert files_in(tmp_path) == ['seed.json', 'world.json']


def test_a_crash_while_writing_the_temp_file_leaves_no_litter(
    tmp_path, seed_file, document, monkeypatch,
):
    """A failure before the rename is just as clean: no half file, no temp file."""
    live = tmp_path / 'world.json'
    store = FileWorldStore(live, seed_path=seed_file)
    real_fdopen = storage.os.fdopen
    monkeypatch.setattr(
        storage.os,
        'fdopen',
        lambda handle, *args, **kwargs: FullDisk(real_fdopen(handle, *args, **kwargs)),
    )

    with pytest.raises(WorldStoreError, match='No space left'):
        store.set_held_by('cube_1', Side.LEFT)

    monkeypatch.undo()
    assert read_document(live) == document
    assert files_in(tmp_path) == ['seed.json', 'world.json']


def test_the_temp_file_lives_beside_its_target(tmp_path, seed_file, monkeypatch):
    """``os.replace`` is only atomic within a filesystem, so the temp is co-located."""
    live = tmp_path / 'nested' / 'world.json'
    live.parent.mkdir()
    store = FileWorldStore(live, seed_path=seed_file)

    seen = []
    real_replace = storage.os.replace

    def spy(source, target):
        seen.append((str(source), str(target)))
        real_replace(source, target)

    monkeypatch.setattr(storage.os, 'replace', spy)
    store.set_held_by('cube_1', Side.RIGHT)

    assert len(seen) == 1
    source, target = seen[0]
    assert os.path.dirname(source) == os.path.dirname(target) == str(live.parent)
    assert source != target
    assert read_document(live).find_object('cube_1').held_by is Side.RIGHT


def test_a_successful_write_leaves_nothing_behind(tmp_path, seed_file):
    """The happy path must not litter the state directory either."""
    live = tmp_path / 'world.json'
    store = FileWorldStore(live, seed_path=seed_file)
    for height in (0.7, 0.8, 0.9):
        store.update_object_pose('cube_1', Pose.from_xyz(1.0, 0.1, height))

    assert files_in(tmp_path) == ['seed.json', 'world.json']
    assert read_document(live).find_object('cube_1').pose == Pose.from_xyz(1.0, 0.1, 0.9)


def test_writing_into_a_missing_directory_is_a_loud_refusal(tmp_path, document):
    """A misconfigured path fails at the write, naming the file it could not write."""
    with pytest.raises(WorldStoreError, match='cannot write world file'):
        write_document(tmp_path / 'nope' / 'world.json', document)


def test_the_in_memory_store_never_touches_the_disk(tmp_path, document, monkeypatch):
    """Persistence is opt-in: a plain WorldStore does no file IO at all."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        store_module,
        'write_document',
        lambda path, document: pytest.fail(f'the in-memory store wrote {path}'),
    )
    store = WorldStore(document)
    store.update_object_pose('cube_1', Pose.from_xyz(0.0, 0.0, 1.0))
    store.set_held_by('cube_1', Side.LEFT)
    store.reset()

    assert files_in(tmp_path) == []
