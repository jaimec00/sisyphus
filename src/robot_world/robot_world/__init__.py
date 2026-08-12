# Copyright (c) 2026 Jaime C.
#
# Use of this source code is governed by an MIT-style
# license that can be found in the LICENSE file or at
# https://opensource.org/licenses/MIT.

"""The world-state store: the map and the object registry, on disk in JSON (D23).

The world used to be a Python literal compiled into the Mock backend: the scene
only changed by editing code, and every mutation died with the process.  This
package is the store the roadmap's step 3 asks for -- locations and objects,
queried and mutated through one small surface, persisted as JSON so the world
survives a restart.

Pure Python and backend-agnostic on purpose: the store is what the MuJoCo swap
inherits, so it holds no reach arithmetic, no gripper book-keeping and no
skill semantics -- only what the world *contains*.

Example::

    from robot_world import FileWorldStore, WorldStore

    store = WorldStore()                            # in memory, shipped scene
    store.find_object('mug_1').pose                 # where the mug is

    live = FileWorldStore('/tmp/world.json')        # same scene, persisted
    live.update_object_pose('mug_1', Pose.from_xyz(0.3, 2.0, 0.75))
    FileWorldStore('/tmp/world.json').find_object('mug_1')   # the moved mug
"""

from robot_world.document import (
    check_world_schema_version,
    WORLD_SCHEMA_VERSION,
    WORLD_SCHEMA_VERSION_KEY,
    WorldDocument,
    WorldObject,
)
from robot_world.storage import (
    default_seed_document,
    DEFAULT_SEED_RESOURCE,
    document_text,
    read_document,
    read_seed_document,
    WorldStoreError,
    write_document,
)
from robot_world.store import FileWorldStore, WorldStore

__all__ = [
    'check_world_schema_version',
    'default_seed_document',
    'DEFAULT_SEED_RESOURCE',
    'document_text',
    'FileWorldStore',
    'read_document',
    'read_seed_document',
    'WORLD_SCHEMA_VERSION',
    'WORLD_SCHEMA_VERSION_KEY',
    'WorldDocument',
    'WorldObject',
    'WorldStore',
    'WorldStoreError',
    'write_document',
]
