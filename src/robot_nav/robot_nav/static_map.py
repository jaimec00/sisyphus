# Copyright (c) 2026 Jaime C.
#
# Use of this source code is governed by an MIT-style
# license that can be found in the LICENSE file or at
# https://opensource.org/licenses/MIT.

"""Derive a ``nav_msgs/OccupancyGrid`` from a world document (D23/RULING 3).

The static map is a **consumer** of ``robot_world``'s single source of truth,
never a second home for scene data (D23/RULING 3): the map is projected from
the world document at runtime, so there is one place a location or object pose
is authored -- the world store -- and nothing to keep in sync.

This module is deliberately *pure*: no rclpy, no node, no graph.  It takes an
already-parsed :class:`~robot_world.WorldDocument` (or its two pieces --
``locations`` and ``objects``) and returns an ``OccupancyGrid``.  That makes
the derivation -- the piece with real rules in it -- unit-testable with a plain
pytest run, which is why the ROS node around it is a thin shell.

The derivation
--------------
The world is 3D; the map is 2D at ``z = 0``.  Every location and every object
projects to a cell:

* ``locations`` are **free-space waypoints** (RULING 3) -- they are the poses
  the robot is asked to drive to, so they must be free, never obstacles.  A
  location's cell (and only its cell, not a footprint) is forced free even if
  an object happens to project nearby; a location is the robot's own goal.
* non-graspable ``objects`` (furniture: ``counter_1``, ``sofa_1``) are
  **occupied** cells with a small square footprint around their ``(x, y)``
  projection -- the counter's top is 1.15 m up but its body is what the base
  must go around.
* graspable ``objects`` (a mug, a plate, a book) are also marked **occupied**;
  the choice is documented here because it is a judgment call.  A graspable
  object at ``z = 0.9--1.15`` sits on a surface, so its own cell is safe to
  traverse under -- but the map has no knowledge of *which* surface, and a
  conservative base planner should slow for or route around a known object
  rather than clip it.  Marking it occupied with the same small footprint is
  the conservative choice and it is the one we take; the footprint is small
  enough (``occupancy_footprint_radius`` ~ 5 cm) that it does not wall off a
  location, and a location that *must* overlap a graspable object is still
  reachable because locations are forced free last.

Bounds and unknowns
-------------------
Map bounds are every location plus every object, padded by ``margin`` (1 m
default, RULING 3).  Inside the bounds every cell starts **free (0)** except
the occupied cells above (**100**); there is no inflation and no unknown
*inside* the rectangle -- the robot's apartment is a known free volume this
close to the authored scene.  ``-1`` (unknown) is therefore reserved for what
the grid cannot describe at all; the derivation writes only the occupied cells
explicitly and lets every other in-bounds cell read as free.  (The grid's own
``info`` rectangle *is* the bounds, so "outside bounds" is simply "not in the
array".)

Frame
-----
The grid is expressed in the ``map`` frame, which by RULING 3 is the world
frame (``map`` == world, and the ``map -> odom`` transform is identity).  The
grid origin is the lower-left cell of the bounds rectangle with the identity
orientation -- the standard ``nav_msgs/OccupancyGrid`` convention.
"""

import math
import struct
from typing import Iterable, Mapping

from nav_msgs.msg import MapMetaData, OccupancyGrid
from robot_skills import Pose
from robot_world import WorldObject

__all__ = [
    'derive_occupancy_grid',
    'occupancy_grid_from_document',
    'FREE',
    'OCCUPIED',
    'UNKNOWN',
    'DEFAULT_RESOLUTION',
    'DEFAULT_MARGIN',
    'DEFAULT_FOOTPRINT_RADIUS',
]

#: Cell values, matching the ``nav_msgs/OccupancyGrid`` convention.
FREE = 0
OCCUPIED = 100
UNKNOWN = -1

#: Metres per cell (RULING 3: ~5 cm).
DEFAULT_RESOLUTION = 0.05
#: Padding added on every side of the locations+objects extent (RULING 3).
DEFAULT_MARGIN = 1.0
#: Radius, in metres, of the square an object occupies around its projection.
#: Small on purpose: objects mark their own spot without walling off a room.
DEFAULT_FOOTPRINT_RADIUS = 0.05


def _footprint_offsets(radius: float, resolution: float):
    """Yield the integer ``(row_offset, col_offset)`` deltas of a footprint.

    The footprint is the square of half-side ``radius`` around the object's
    projection, so the deltas are every cell index within ``radius`` of the
    centre cell.  A zero ``radius`` still yields the ``(0, 0)`` centre delta
    (a zero-extent object is still an object), so an object's own cell is
    always marked.

    The offsets are *cell indices*, not re-projected coordinates: marking in
    cell space keeps the producer and any consumer of the grid in agreement
    even when the published ``info.resolution`` round-trips through the
    message's ``float32`` (a coordinate that lands exactly on a cell boundary
    would otherwise round to a neighbouring cell on the consumer's side).
    """
    if radius <= 0.0:
        # A zero-extent object marks only its own cell (the centre delta),
        # never a surrounding square.
        yield 0, 0
        return
    span = int(math.ceil(radius / resolution))
    for row_offset in range(-span, span + 1):
        for col_offset in range(-span, span + 1):
            yield row_offset, col_offset


def derive_occupancy_grid(
    locations: Mapping[str, Pose],
    objects: Iterable[WorldObject] = (),
    resolution: float = DEFAULT_RESOLUTION,
    margin: float = DEFAULT_MARGIN,
    footprint_radius: float = DEFAULT_FOOTPRINT_RADIUS,
) -> OccupancyGrid:
    """Return the occupancy grid for ``locations`` and ``objects``.

    ``locations`` is the world's named reference poses (free waypoints);
    ``objects`` is its object registry (each an occupied cell + footprint).
    Bounds are every pose padded by ``margin``; cells outside the bounds are
    simply absent (there is no -1 *rectangle*), and in-bounds cells are free
    unless an object occupies them.

    ``resolution`` is metres per cell; it must be positive and finite.  The
    returned grid has ``map``-frame semantics (see the module docstring).

    The resolution is snapped to ``float32`` before any cell arithmetic:
    ``OccupancyGrid.info.resolution`` is a ``float32`` on the wire, so a
    producer that computed cells at full double precision would disagree with
    every consumer that reads the published resolution back.  A pose landing
    exactly on a cell boundary (``0.05`` -- ``0.05`` m/cell is the default)
    would then be reported in the neighbouring cell.  Normalising once, up
    front, keeps the grid's own ``info`` and its cell contents consistent for
    anyone who reads it.
    """
    if not isinstance(resolution, (int, float)) or not math.isfinite(resolution):
        raise ValueError(f'resolution must be a finite number, got {resolution!r}')
    if resolution <= 0.0:
        raise ValueError(f'resolution must be positive, got {resolution!r}')
    resolution = float(struct.unpack('f', struct.pack('f', resolution))[0])
    if not isinstance(margin, (int, float)) or not math.isfinite(margin):
        raise ValueError(f'margin must be a finite number, got {margin!r}')
    if margin < 0.0:
        raise ValueError(f'margin must be non-negative, got {margin!r}')

    objects = list(objects)
    xs = [pose.position.x for pose in locations.values()]
    ys = [pose.position.y for pose in locations.values()]
    xs += [obj.pose.position.x for obj in objects]
    ys += [obj.pose.position.y for obj in objects]
    if not xs:
        # A world with no locations is refused by WorldDocument itself, but the
        # pure function takes the pieces and may be handed empty ones; an
        # empty grid is more honest than an invented extent.
        grid = OccupancyGrid()
        grid.header.frame_id = 'map'
        grid.info = MapMetaData()
        grid.info.resolution = float(resolution)
        grid.info.width = 0
        grid.info.height = 0
        grid.info.origin.orientation.w = 1.0
        grid.data = []
        return grid

    min_x = min(xs) - margin
    min_y = min(ys) - margin
    max_x = max(xs) + margin
    max_y = max(ys) + margin

    width = int(math.ceil((max_x - min_x) / resolution))
    height = int(math.ceil((max_y - min_y) / resolution))
    width = max(width, 1)
    height = max(height, 1)

    def to_cell(x: float, y: float):
        col = int((x - min_x) // resolution)
        row = int((y - min_y) // resolution)
        return row, col

    data = [FREE] * (width * height)

    # Objects first (occupied), then locations forced free: a location is a
    # goal the robot must be able to stand on, so it wins over an object
    # projection that happens to coincide with it (RULING 3).
    if footprint_radius < 0.0:
        raise ValueError(
            f'footprint_radius must be non-negative, got {footprint_radius!r}')
    for obj in objects:
        center_row, center_col = to_cell(obj.pose.position.x, obj.pose.position.y)
        for row_offset, col_offset in _footprint_offsets(
                footprint_radius, resolution):
            row = center_row + row_offset
            col = center_col + col_offset
            if 0 <= row < height and 0 <= col < width:
                data[row * width + col] = OCCUPIED

    for pose in locations.values():
        row, col = to_cell(pose.position.x, pose.position.y)
        if 0 <= row < height and 0 <= col < width:
            data[row * width + col] = FREE

    grid = OccupancyGrid()
    grid.header.frame_id = 'map'
    grid.info = MapMetaData()
    grid.info.resolution = float(resolution)
    grid.info.width = width
    grid.info.height = height
    grid.info.origin.position.x = float(min_x)
    grid.info.origin.position.y = float(min_y)
    grid.info.origin.position.z = 0.0
    grid.info.origin.orientation.w = 1.0
    grid.data = data
    return grid


def occupancy_grid_from_document(
    document,
    resolution: float = DEFAULT_RESOLUTION,
    margin: float = DEFAULT_MARGIN,
    footprint_radius: float = DEFAULT_FOOTPRINT_RADIUS,
) -> OccupancyGrid:
    """Derive the grid for a whole :class:`~robot_world.WorldDocument`.

    A one-line adapter so callers holding the document (the map node, tests)
    never re-split it by hand: the derivation itself takes the two pieces.
    """
    return derive_occupancy_grid(
        document.locations,
        document.objects,
        resolution=resolution,
        margin=margin,
        footprint_radius=footprint_radius,
    )
