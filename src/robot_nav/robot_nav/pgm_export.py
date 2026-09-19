# Copyright (c) 2026 Jaime C.
#
# Use of this source code is governed by an MIT-style
# license that can be found in the LICENSE file or at
# https://opensource.org/licenses/MIT.

"""Render a derived grid to the PGM+YAML pair ``nav2_map_server`` reads.

This exists for **one reason** (RULING 5): PR1 must prove a *genuine Nav2*
lifecycle node configures and activates headless.  The intended node is
``nav2_map_server``, and it loads its grid from a PGM image plus a YAML
sidecar, not from a ROS topic.  Our operational map is derived at runtime
(RULING 3) and published on ``/map`` by :mod:`robot_nav.map_node`; the
``nav2_map_server`` in ``nav.launch.py`` is a **demonstration** of the Nav2
lifecycle machinery, and to give it the *same* map we render the derived grid
to a throwaway PGM at launch time rather than checking a second copy of the
map into the repo.

The PGM is written under the runtime directory (``~/.ros``, the repo's
runtime-file convention), never in the source tree, and it is regenerated from
the world on every launch -- so ``robot_world`` remains the single source of
truth and the PGM is a rendering of it, exactly like the ``/map`` topic.

The rendering is deliberately dumb: the OccupancyGrid's own convention is
``0`` free, ``100`` occupied, ``-1`` unknown, and PGM's is ``0`` black ..
``255`` white, so ``unknown -> 205`` (Nav2's own convention), ``free -> 254``
and ``occupied -> 0``.  No inflation, no threshold: this is the same grid, test
artifacts included.
"""

import os

from nav_msgs.msg import OccupancyGrid

__all__ = ['write_pgm', 'write_map_yaml', 'export_grid']

#: Nav2's grey level for "unknown" (``-1``) cells in a PGM.
UNKNOWN_LEVEL = 205
#: Grey level for free (``0``) cells -- near-white, as Nav2 writes them.
FREE_LEVEL = 254
#: Grey level for occupied (``100``) cells.
OCCUPIED_LEVEL = 0


def _level(value: int) -> int:
    """Return the PGM grey level for one occupancy value."""
    if value < 0:
        return UNKNOWN_LEVEL
    # 100 is occupied, 0 is free; anything between is a probability, drawn
    # linearly so a partially-occupied cell is a mid grey rather than a guess.
    if value >= 100:
        return OCCUPIED_LEVEL
    return int(round(FREE_LEVEL - (value / 100.0) * (FREE_LEVEL - OCCUPIED_LEVEL)))


def write_pgm(grid: OccupancyGrid, path: str) -> None:
    """Write ``grid`` to ``path`` as a binary PGM (P5).

    The grid's row 0 is the *bottom* of the map (``OccupancyGrid`` origin is
    the lower-left, +y up) while PGM row 0 is the *top* (image convention), so
    rows are emitted in reverse.
    """
    width = grid.info.width
    height = grid.info.height
    data = list(grid.data)
    with open(path, 'wb') as handle:
        handle.write(b'P5\n%d %d\n255\n' % (width, height))
        rows = bytearray()
        for row in range(height - 1, -1, -1):
            base = row * width
            for col in range(width):
                rows.append(_level(data[base + col]))
        handle.write(bytes(rows))


def write_map_yaml(grid: OccupancyGrid, pgm_path: str, yaml_path: str) -> None:
    """Write the ``nav2_map_server`` YAML sidecar naming ``pgm_path``.

    ``origin`` is the grid's lower-left cell in the map frame, and
    ``negate``/``occupied_thresh``/``free_thresh`` are Nav2's defaults for a
    trinary PGM.
    """
    origin = grid.info.origin
    text = (
        'image: %s\n'
        'resolution: %r\n'
        'origin: [%r, %r, %r]\n'
        'negate: 0\n'
        'occupied_thresh: 0.65\n'
        'free_thresh: 0.196\n'
        % (os.path.basename(pgm_path), grid.info.resolution,
           origin.position.x, origin.position.y, origin.position.z)
    )
    with open(yaml_path, 'w') as handle:
        handle.write(text)


def export_grid(grid: OccupancyGrid, base_path: str) -> str:
    """Write ``grid`` as ``<base_path>.pgm`` + ``<base_path>.yaml``; return the YAML.

    The return value is what ``nav2_map_server``'s ``yaml_filename`` wants.
    """
    pgm_path = base_path + '.pgm'
    yaml_path = base_path + '.yaml'
    write_pgm(grid, pgm_path)
    write_map_yaml(grid, pgm_path, yaml_path)
    return yaml_path
