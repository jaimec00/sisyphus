# Copyright (c) 2026 Jaime C.
#
# Use of this source code is governed by an MIT-style
# license that can be found in the LICENSE file or at
# https://opensource.org/licenses/MIT.

"""The world store: the queried, mutable half of the world model (D23).

:class:`WorldStore` owns **what the world contains** -- the map (named
locations) and the object registry (id, label, pose, graspable, held-by) --
and nothing about *how a skill changes it*.  Reach arithmetic, gripper
book-keeping and refusal codes stay in the backend, where the physics lives;
the store would otherwise grow skill semantics and stop being the layer that
survives the Mock -> MuJoCo swap.

Two flavours, one surface:

* :class:`WorldStore` -- in-memory.  ``reset()`` returns to the document it was
  built from.  Touches no file, ever.
* :class:`FileWorldStore` -- backed by a live-state JSON file, seeded from a
  read-only seed file (the shipped one by default).  Every mutation is flushed
  to disk atomically, so the world survives the process.

Persistence is **opt-in**: a bare :class:`WorldStore` (and therefore a bare
``MockBackend()``) is exactly as deterministic and file-free as it was before
this store existed.  A test or a deployment that wants a persisted world says
so by constructing a :class:`FileWorldStore`.

Writes and batches
------------------
Outside a batch every mutation commits immediately.  Inside
``with store.batch():`` mutations accumulate and commit **once** on exit, which
is what lets a single skill -- which can move up to three objects, counting the
carried ones -- become a single atomic disk transition rather than three.
The commit happens on exceptional exit too, so the file never lags behind what
the caller can already observe in memory.
"""

from contextlib import contextmanager
import os
from pathlib import Path
from types import MappingProxyType
from typing import Iterator, Mapping

from robot_skills import Pose, Side
from robot_world.document import WorldDocument, WorldObject
from robot_world.storage import (
    read_document,
    read_seed_document,
    WorldStoreError,
    write_document,
)

__all__ = ['FileWorldStore', 'WorldStore']


class WorldStore:
    """An in-memory, queryable registry of locations and objects.

    Example::

        store = WorldStore()                       # the shipped demo apartment
        store.find_object('mug_1').pose            # where the mug is
        store.update_object_pose('mug_1', Pose.from_xyz(0.3, 2.0, 0.75))
        store.reset()                              # back to the seed scene
    """

    def __init__(self, document: WorldDocument | None = None) -> None:
        """Create a store holding ``document`` (the shipped seed by default)."""
        if document is not None and not isinstance(document, WorldDocument):
            raise TypeError(
                f'document must be a WorldDocument, got {type(document).__name__}')
        self._seed = document if document is not None else read_seed_document()
        self._batch_depth = 0
        self._pending = False
        self._load(self._seed)

    # -- queries -----------------------------------------------------------

    def locations(self) -> Mapping[str, Pose]:
        """Return the named locations as a read-only mapping.

        Read-only for this iteration: the map is quasi-static and no skill
        mutates it (adding a location is a later feature, not a chore step).
        """
        return MappingProxyType(self._locations)

    def location(self, name: str) -> Pose | None:
        """Return the pose of a named location, or ``None`` if unknown."""
        return self._locations.get(name)

    def objects(self) -> tuple[WorldObject, ...]:
        """Return every registered object, in document order."""
        return tuple(self._objects.values())

    def find_object(self, object_id: str) -> WorldObject | None:
        """Return one registered object, or ``None`` if it is not in the scene.

        Named to match :meth:`robot_skills.Observation.find_object`, the same
        lookup one layer up.
        """
        return self._objects.get(object_id)

    @property
    def start_location(self) -> str:
        """Return the name of the location a robot comes up at."""
        return self._start_location

    @property
    def start_column_height(self) -> float:
        """Return the column height a robot comes up at, in metres."""
        return self._start_column_height

    def document(self) -> WorldDocument:
        """Return an immutable snapshot of the whole scene, as it would be written."""
        return WorldDocument(
            locations=dict(self._locations),
            start_location=self._start_location,
            objects=self.objects(),
            start_column_height=self._start_column_height,
        )

    def seed_document(self) -> WorldDocument:
        """Return the scene :meth:`reset` restores (this store's seed)."""
        return self._seed

    # -- mutations ---------------------------------------------------------

    def update_object_pose(self, object_id: str, pose: Pose) -> None:
        """Move a registered object to a new world-frame pose."""
        if not isinstance(pose, Pose):
            raise TypeError(f'pose must be a Pose, got {type(pose).__name__}')
        item = self._require(object_id)
        if item.pose == pose:
            return
        self._replace(WorldObject(
            object_id=item.object_id,
            label=item.label,
            pose=pose,
            graspable=item.graspable,
            held_by=item.held_by,
        ))

    def set_held_by(self, object_id: str, side: Side | None) -> None:
        """Record which gripper holds an object (``None`` = nobody holds it).

        The store records the fact; deciding it -- and keeping it agreeing with
        the gripper's own book-keeping, which ``Observation`` enforces -- is the
        backend's job, because "my hand is full" is a statement about the robot.
        """
        item = self._require(object_id)
        if item.held_by == side:
            return
        self._replace(WorldObject(
            object_id=item.object_id,
            label=item.label,
            pose=item.pose,
            graspable=item.graspable,
            held_by=side,
        ))

    def add_object(self, item: WorldObject) -> None:
        """Register a new object, refusing an id that is already taken."""
        if not isinstance(item, WorldObject):
            raise TypeError(
                f'item must be a WorldObject, got {type(item).__name__}')
        if item.object_id in self._objects:
            raise WorldStoreError(
                f'the world store already holds an object {item.object_id!r}')
        self._objects[item.object_id] = item
        self._touch()

    def remove_object(self, object_id: str) -> None:
        """Drop an object from the registry (it left the scene)."""
        self._require(object_id)
        del self._objects[object_id]
        self._touch()

    def reset(self) -> None:
        """Restore the seed scene, discarding every mutation since.

        For a :class:`FileWorldStore` this re-reads the seed **file** and
        rewrites the live-state file with it, so a corrupted or drifted live
        state is recovered from ground truth rather than from memory.
        """
        self._load(self.seed_document())
        self._touch()

    @contextmanager
    def batch(self) -> Iterator['WorldStore']:
        """Group mutations so they commit to disk once, on exit.

        Nests: only the outermost batch commits.  A batch left by an exception
        still commits, because the in-memory scene has already changed and a
        file that silently disagreed with it would be worse than one that
        records a half-finished skill (which the next load reconciles anyway).
        """
        self._batch_depth += 1
        try:
            yield self
        finally:
            self._batch_depth -= 1
            if self._batch_depth == 0 and self._pending:
                self._flush()

    # -- internals ---------------------------------------------------------

    def _load(self, document: WorldDocument) -> None:
        """Adopt ``document`` as the current scene (without committing)."""
        self._locations: dict[str, Pose] = dict(document.locations)
        self._start_location = document.start_location
        self._start_column_height = document.start_column_height
        self._objects: dict[str, WorldObject] = {
            item.object_id: item for item in document.objects
        }

    def _require(self, object_id: str) -> WorldObject:
        """Return a registered object or refuse loudly."""
        item = self._objects.get(object_id)
        if item is None:
            raise WorldStoreError(
                f'no object {object_id!r} in the world store; registered: '
                f'{", ".join(sorted(self._objects)) or "(none)"}')
        return item

    def _replace(self, item: WorldObject) -> None:
        """Swap one registry entry for an updated copy, keeping its position."""
        self._objects[item.object_id] = item
        self._touch()

    def _touch(self) -> None:
        """Mark the scene dirty, committing now unless a batch is open."""
        self._pending = True
        if self._batch_depth == 0:
            self._flush()

    def _flush(self) -> None:
        """Commit the pending mutations and clear the dirty flag."""
        self._pending = False
        self._commit()

    def _commit(self) -> None:
        """Persist the current scene.  In memory there is nothing to do."""


class FileWorldStore(WorldStore):
    """A :class:`WorldStore` whose scene lives in a JSON file on disk.

    Example::

        store = FileWorldStore('/var/lib/robot/world.json')   # shipped seed
        backend = MockBackend(store=store)                    # persisted world

    Startup rules (D23):

    * **live file missing** -- create it from the seed and carry on; that is
      the expected first run of a fresh checkout.
    * **live file corrupt** -- raise :class:`~robot_world.WorldStoreError`.
      Never "repair" it by overwriting with the seed: that would destroy the
      one piece of evidence an operator has.
    * **seed missing or corrupt** -- always a hard error, seed file or shipped
      resource alike.  A broken seed is a broken install, not a runtime state.

    See :mod:`robot_world.storage` for the atomic-write mechanics and for the
    two limits accepted here: no ``fsync``, and no cross-process locking.
    """

    def __init__(
        self,
        live_path: str | os.PathLike[str],
        seed_path: str | os.PathLike[str] | None = None,
    ) -> None:
        """Open ``live_path``, seeding it from ``seed_path`` (or the shipped seed)."""
        self._live_path = Path(live_path)
        self._seed_path = Path(seed_path) if seed_path is not None else None
        seed = self.seed_document()
        if self._live_path.exists():
            document = read_document(self._live_path)
        else:
            document = seed
            write_document(self._live_path, document)
        super().__init__(document)

    @property
    def live_path(self) -> Path:
        """Return the path of the live-state file this store writes."""
        return self._live_path

    @property
    def seed_path(self) -> Path | None:
        """Return the seed file's path, or ``None`` for the shipped seed."""
        return self._seed_path

    def seed_document(self) -> WorldDocument:
        """Re-read the seed from disk, so ``reset()`` restores ground truth."""
        return read_seed_document(self._seed_path)

    def _commit(self) -> None:
        """Write the whole scene to the live-state file, atomically."""
        write_document(self._live_path, self.document())
