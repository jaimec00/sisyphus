# Copyright (c) 2026 Jaime C.
#
# Use of this source code is governed by an MIT-style
# license that can be found in the LICENSE file or at
# https://opensource.org/licenses/MIT.

"""Reading and (atomically) writing world documents on disk.

Two files, one schema (D23): a **read-only seed** shipped inside this package,
and a **live-state file** the running robot writes.  Keeping them apart is what
makes ``reset()`` honest -- the first mutation cannot destroy the pristine
scene, because the scene is not the file being written.

Atomic writes
-------------
:func:`write_document` never truncates the live file in place.  It writes a
temporary file **in the target's own directory** (``os.replace`` is only atomic
within one filesystem, so a temp under ``/tmp`` would silently degrade to a
copy), then renames it over the target.  A reader therefore always sees either
the whole old document or the whole new one, and a process that dies mid-write
leaves the previous file untouched.  The temp file is removed on any failure,
so a crashed write leaves no litter either.

Known limits, deliberately not solved here (see the feature docs):

* **No ``fsync``.**  ``os.replace`` gives atomicity against a *crashed process*,
  not durability against a power cut with dirty page cache.  Out of scope at
  this scale.
* **No cross-process lock.**  Two processes pointed at the same live file can
  race on ``os.replace``: last writer wins, so an update can be lost (the file
  is never corrupted).  The deployment model is a single robot-side service
  running one task at a time (D16/D21), and ``robot_mcp`` already serializes
  calls within a process.
"""

from functools import lru_cache
from importlib import resources
import json
import os
from pathlib import Path
import tempfile

from robot_skills import SerializationError
from robot_world.document import WorldDocument

__all__ = [
    'DEFAULT_SEED_RESOURCE',
    'default_seed_document',
    'document_text',
    'read_document',
    'read_seed_document',
    'write_document',
    'WorldStoreError',
]

#: The seed shipped inside this package (see ``setup.py``'s ``package_data``).
DEFAULT_SEED_RESOURCE = 'default_world.json'

#: Package the seed resource lives in.
_RESOURCE_PACKAGE = 'robot_world'


class WorldStoreError(ValueError):
    """Raised when a world file cannot be read, parsed or written.

    A :class:`ValueError` subclass, matching this repo's other loud-refusal
    errors (``SerializationError``, ``SafetyConfigError``).  A corrupt world
    file is always this, never a silent repair: overwriting it with the seed
    would destroy the evidence that something went wrong.
    """


def _parse(text: str, *, source: str) -> WorldDocument:
    """Parse world-document JSON, reporting where it came from."""
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise WorldStoreError(f'{source}: invalid JSON: {exc}') from exc
    if not isinstance(data, dict):
        raise WorldStoreError(
            f'{source}: expected a JSON object, got {type(data).__name__}')
    try:
        return WorldDocument.from_dict(data)
    except SerializationError as exc:
        raise WorldStoreError(f'{source}: {exc}') from exc


def read_document(path: str | os.PathLike[str]) -> WorldDocument:
    """Read and parse a world document from ``path``.

    Raises :class:`WorldStoreError` for a missing, unreadable, malformed or
    schema-violating file -- one exception type for "this file is not a world".
    """
    path = Path(path)
    try:
        text = path.read_text(encoding='utf-8')
    except OSError as exc:
        raise WorldStoreError(f'cannot read world file {str(path)!r}: {exc}') from exc
    return _parse(text, source=f'world file {str(path)!r}')


def read_seed_document(path: str | os.PathLike[str] | None = None) -> WorldDocument:
    """Read the seed document: ``path`` if given, else the shipped default.

    A missing or corrupt seed is always a hard :class:`WorldStoreError` -- the
    shipped one is a package resource, so its absence is a broken install
    rather than a runtime condition, and an operator-supplied one that cannot
    be read is a misconfiguration worth failing on immediately.
    """
    if path is not None:
        return read_document(path)
    return default_seed_document()


@lru_cache(maxsize=1)
def default_seed_document() -> WorldDocument:
    """Return the scene shipped with this package (the demo apartment).

    Read through :mod:`importlib.resources` so it works from a source
    checkout, a symlink-installed colcon build and a wheel alike, exactly as
    ``robot_brain``'s OpenClaw config and ``robot_safety``'s ``limits.yaml``
    already do.  Read-only by construction: nothing ever writes here.

    Memoized, like ``robot_brain.agent.config_fragment``: the file cannot
    change under a running robot, and every ``MockBackend()`` would otherwise
    pay a resource read plus a full parse and validation.  Sharing one instance
    is safe because a :class:`WorldDocument` is frozen all the way down (a
    ``MappingProxyType`` of locations, a tuple of frozen objects) and stores
    copy it into their own state rather than holding it.
    """
    resource = resources.files(_RESOURCE_PACKAGE) / DEFAULT_SEED_RESOURCE
    try:
        text = resource.read_text(encoding='utf-8')
    except OSError as exc:  # pragma: no cover - only on a broken install
        raise WorldStoreError(
            f'the shipped world seed {DEFAULT_SEED_RESOURCE!r} is missing from '
            f'{_RESOURCE_PACKAGE}: {exc}') from exc
    return _parse(text, source=f'shipped seed {DEFAULT_SEED_RESOURCE!r}')


def document_text(document: WorldDocument) -> str:
    """Return the canonical file text for ``document`` (what a write emits).

    Indented and newline-terminated so a live-state file stays readable (and
    diffable) by the human debugging what the robot thinks the room looks like.
    """
    return json.dumps(document.to_dict(), indent=2) + '\n'


def write_document(
    path: str | os.PathLike[str],
    document: WorldDocument,
) -> None:
    """Write ``document`` to ``path`` atomically (temp file + ``os.replace``).

    The temp file is created in the target's directory, so the rename stays
    within one filesystem and is therefore atomic; it is removed again if
    anything goes wrong, including a failure of the rename itself.  Either way
    the file at ``path`` is only ever a complete document.
    """
    path = Path(path)
    directory = path.parent if str(path.parent) else Path('.')
    text = document_text(document)
    try:
        handle, temporary = tempfile.mkstemp(
            dir=str(directory), prefix=f'.{path.name}.', suffix='.tmp')
    except OSError as exc:
        raise WorldStoreError(
            f'cannot write world file {str(path)!r}: {exc}') from exc
    try:
        with os.fdopen(handle, 'w', encoding='utf-8') as stream:
            stream.write(text)
        os.replace(temporary, path)
    except BaseException as exc:
        # The rename never happened (or never completed), so ``path`` still
        # holds the previous document.  Drop the half-written temp file rather
        # than leaving it beside the real one.
        try:
            os.unlink(temporary)
        except OSError:  # pragma: no cover - already gone
            pass
        if isinstance(exc, OSError):
            raise WorldStoreError(
                f'cannot write world file {str(path)!r}: {exc}') from exc
        raise
