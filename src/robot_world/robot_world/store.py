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

* :class:`WorldStore` -- in-memory.  ``reset()`` returns to its seed, which is
  the document it was built from unless one is passed separately.  Never
  *writes* a file; constructed with no document it reads the shipped seed once,
  and after that touches nothing.
* :class:`FileWorldStore` -- backed by a live-state JSON file, seeded from a
  read-only seed file (the shipped one by default).  Every mutation is flushed
  to disk atomically, so the world survives the process.

Persistence is **opt-in**: a bare :class:`WorldStore` (and therefore a bare
``MockBackend()``) is exactly as deterministic as it was before this store
existed, and **writes nothing** -- the only file it opens is the read-only
shipped seed.  A test or a deployment that wants a persisted world says so by
constructing a :class:`FileWorldStore`.

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
from robot_world.document import duplicate_hold_sides, WorldDocument, WorldObject
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

    def __init__(
        self,
        document: WorldDocument | None = None,
        *,
        seed: WorldDocument | None = None,
    ) -> None:
        """Create a store holding ``document`` (the shipped seed by default).

        ``seed`` is the scene :meth:`reset` restores, and defaults to
        ``document`` -- the whole truth for an in-memory store, whose starting
        scene *is* its ground truth.  A store that comes up on a scene which has
        already drifted from ground truth (a :class:`FileWorldStore` reopening a
        live file it wrote days ago) passes the two separately, so ``_seed`` is
        never quietly "whatever we happened to load".
        """
        if document is not None and not isinstance(document, WorldDocument):
            raise TypeError(
                f'document must be a WorldDocument, got {type(document).__name__}')
        if seed is not None and not isinstance(seed, WorldDocument):
            raise TypeError(
                f'seed must be a WorldDocument, got {type(seed).__name__}')
        if document is None:
            document = read_seed_document()
        self._seed = seed if seed is not None else document
        self._batch_depth = 0
        self._pending = False
        self._load(document)

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

    @property
    def pending_write(self) -> bool:
        """Return whether mutations are still waiting to be committed.

        True inside an open :meth:`batch`, and -- for a
        :class:`FileWorldStore` -- after a commit that failed, until one
        succeeds.  Always False for an in-memory store outside a batch, whose
        commit cannot fail.
        """
        return self._pending

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

        A gripper that already holds something is refused, immediately and
        including inside a :meth:`batch`, because a direct reader of the store
        sees the in-memory scene rather than the committed file.  Moving a hold
        is therefore two calls, in the order the physical robot must also use:
        clear the old object (``set_held_by(old, None)``), then set the new one.
        """
        item = self._require(object_id)
        if item.held_by == side:
            return
        updated = WorldObject(
            object_id=item.object_id,
            label=item.label,
            pose=item.pose,
            graspable=item.graspable,
            held_by=side,
        )
        self._refuse_hold_conflict(updated)
        self._replace(updated)

    def add_object(self, item: WorldObject) -> None:
        """Register a new object, refusing an id that is already taken."""
        if not isinstance(item, WorldObject):
            raise TypeError(
                f'item must be a WorldObject, got {type(item).__name__}')
        if item.object_id in self._objects:
            raise WorldStoreError(
                f'the world store already holds an object {item.object_id!r}')
        self._refuse_hold_conflict(item)
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

    def _refuse_hold_conflict(self, item: WorldObject) -> None:
        """Refuse ``item`` if it would leave one gripper holding two objects.

        Called *before* the registry changes, so a refused mutation leaves the
        scene byte-identical -- a store that raised half way would be worse than
        one that never tried.  The rule itself lives in
        :func:`~robot_world.document.duplicate_hold_sides`, the same scan
        :class:`~robot_world.WorldDocument` validates whole scenes with; only
        the refusal wording is the store's own.
        """
        if item.held_by is None:
            return
        others = [
            entry for entry in self._objects.values()
            if entry.object_id != item.object_id
        ]
        if item.held_by not in duplicate_hold_sides((*others, item)):
            return
        holder = next(entry for entry in others if entry.held_by is item.held_by)
        raise WorldStoreError(
            f'cannot record {item.object_id!r} as held by the {item.held_by.value} '
            f'gripper: it already holds {holder.object_id!r}; release that first '
            f'(set_held_by({holder.object_id!r}, None))')

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
        """Commit the pending mutations, clearing the dirty flag only on success.

        Order matters: a commit that fails (full disk, read-only mount, the
        state directory removed under the process) must leave the store
        *dirty*, or it would claim to be in sync with a file that is one skill
        stale and never try again.  Because a commit writes the whole document,
        staying dirty means the next mutation of any kind repairs the file.
        """
        self._commit()
        self._pending = False

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
        self._refuse_seeding_from_the_live_file()
        seed = self.seed_document()
        if self._live_path.exists():
            document = read_document(self._live_path)
        else:
            document = seed
            write_document(self._live_path, document)
        super().__init__(document, seed=seed)

    @property
    def live_path(self) -> Path:
        """Return the path of the live-state file this store writes."""
        return self._live_path

    @property
    def seed_path(self) -> Path | None:
        """Return the seed file's path, or ``None`` for the shipped seed."""
        return self._seed_path

    def seed_document(self) -> WorldDocument:
        """Re-read the seed from disk, so ``reset()`` restores ground truth.

        The re-read is the D23 mechanism, not an optimization to be removed:
        the seed is a *file*, so replacing that file must change what
        ``reset()`` restores -- which is how an operator re-seeds a running
        robot, and what ``test_reset_restores_from_the_seed_file_not_from_memory``
        pins.  It is also how ``__init__`` obtains the seed, before ``_seed``
        exists, so this is not an attribute read waiting to happen.
        """
        return read_seed_document(self._seed_path)

    def _refuse_seeding_from_the_live_file(self) -> None:
        """Refuse a seed path that is the live file: ``reset()`` would be a no-op.

        Pointing both at one file makes the seed read back whatever the robot
        last wrote, so ``reset()`` silently restores the *current* scene and
        acceptance criterion 3 quietly stops holding.  Everything else about a
        world file fails loudly; this must too.  Paths are compared resolved,
        not as written, so ``./world.json`` and an absolute path (or a symlink
        to one) are caught as well.
        """
        if self._seed_path is None:
            return
        live, seed = self._live_path, self._seed_path
        same = live.resolve() == seed.resolve()
        if not same and live.exists() and seed.exists():
            same = live.samefile(seed)
        if same:
            raise WorldStoreError(
                f'the seed {str(seed)!r} and the live-state file {str(live)!r} are '
                'the same file; the seed must be a separate read-only file, or '
                'reset() would restore whatever the robot last wrote')

    def _commit(self) -> None:
        """Write the whole scene to the live-state file, atomically."""
        write_document(self._live_path, self.document())
