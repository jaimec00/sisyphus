# Copyright (c) 2026 Jaime C.
#
# Use of this source code is governed by an MIT-style
# license that can be found in the LICENSE file or at
# https://opensource.org/licenses/MIT.

"""Pure unit tests for the world-document -> OccupancyGrid derivation (RULING 3).

``static_map.py`` is the one piece of this package with real rules in it, and
it is deliberately ROS-free (no rclpy, no graph), so it can be held to account
with a plain pytest run -- no launch, no domain, no nodes.  These tests use the
**real shipped seed world** (``robot_world.default_seed_document()``, the same
scene ``default_world.json`` holds) rather than a hand-built fixture, so the
derivation is exercised against the shape it actually meets in the bringup:
four locations (charger/kitchen/table/living_room), non-graspable furniture
(``counter_1``, ``sofa_1``) and graspable tabletop objects
(mug/plate/bowl/book/cup).

The claims, from RULING 3:

* locations project to **free** cells -- they are the robot's goal waypoints,
  and a goal it cannot stand on would be useless;
* non-graspable furniture projects to **occupied** cells;
* bounds cover every location and object plus the margin, at the requested
  resolution;
* ``z`` is ignored (the map is 2D at ``z = 0``): a pose lifted a metre up
  projects to the same cell as one on the floor.
"""

import math

from nav_msgs.msg import OccupancyGrid

from robot_nav.static_map import (
    derive_occupancy_grid,
    FREE,
    occupancy_grid_from_document,
    OCCUPIED,
)
from robot_world import default_seed_document

#: The shipped scene's expected members (asserted, so a seed change is loud).
EXPECTED_LOCATIONS = {'charger', 'kitchen', 'table', 'living_room'}
EXPECTED_FURNITURE = {'counter_1', 'sofa_1'}


def _cell(grid, x, y):
    """Return the occupancy value of the cell holding world ``(x, y)``.

    The inverse of the derivation's ``to_cell``: offset from the origin (the
    lower-left cell) and divide by the resolution.  Fails loudly if the point
    falls outside the grid, so a bounds bug shows up at the call site.
    """
    col = int((x - grid.info.origin.position.x) // grid.info.resolution)
    row = int((y - grid.info.origin.position.y) // grid.info.resolution)
    assert 0 <= row < grid.info.height, (
        'row %d out of [0, %d) for y=%r' % (row, grid.info.height, y))
    assert 0 <= col < grid.info.width, (
        'col %d out of [0, %d) for x=%r' % (col, grid.info.width, x))
    return grid.data[row * grid.info.width + col]


def test_seed_scene_has_the_shape_the_tests_assume():
    """The shipped seed still looks like the scene these tests reason about.

    The derivation is a consumer of the world (D23); if the seed's locations or
    furniture change, every expectation below is measured against a different
    scene.  Asserting the shape up front turns that into one clear failure
    instead of a scatter of confusing cell misses.
    """
    document = default_seed_document()
    assert set(document.locations) == EXPECTED_LOCATIONS
    furniture = {obj.object_id for obj in document.objects if not obj.graspable}
    assert furniture == EXPECTED_FURNITURE
    # The start location is the world origin -- which is what makes the
    # ground-truth base pose the identity (RULING 1).
    assert document.start_location == 'charger'
    pose = document.locations['charger'].position
    assert (pose.x, pose.y, pose.z) == (0.0, 0.0, 0.0)


def test_locations_are_free_cells_not_obstacles():
    """Every named location is a free cell (RULING 3)."""
    document = default_seed_document()
    grid = occupancy_grid_from_document(document)
    assert isinstance(grid, OccupancyGrid)
    for name, pose in document.locations.items():
        value = _cell(grid, pose.position.x, pose.position.y)
        assert value == FREE, (
            'location %r at (%r, %r) is %d, not free'
            % (name, pose.position.x, pose.position.y, value))


def test_furniture_is_occupied():
    """Non-graspable furniture projects to occupied cells (RULING 3)."""
    document = default_seed_document()
    grid = occupancy_grid_from_document(document)
    for obj in document.objects:
        if obj.graspable:
            continue
        value = _cell(grid, obj.pose.position.x, obj.pose.position.y)
        assert value == OCCUPIED, (
            'furniture %r at (%r, %r) is %d, not occupied'
            % (obj.object_id, obj.pose.position.x, obj.pose.position.y, value))


def test_graspable_objects_are_occupied():
    """Graspable objects are occupied too -- the conservative choice.

    Documented in ``static_map``: a graspable object sits on a surface the map
    cannot see, so its own cell is marked occupied rather than assumed
    traversable.  The footprint is small, and locations win the cell if they
    coincide (see the next test).
    """
    document = default_seed_document()
    grid = occupancy_grid_from_document(document)
    graspables = [obj for obj in document.objects if obj.graspable]
    assert graspables, 'the seed scene should hold graspable objects'
    for obj in graspables:
        value = _cell(grid, obj.pose.position.x, obj.pose.position.y)
        assert value == OCCUPIED, (
            'graspable %r at (%r, %r) is %d, not occupied'
            % (obj.object_id, obj.pose.position.x, obj.pose.position.y, value))


def test_a_location_overlapping_an_object_is_still_free():
    """A location forced onto an object's cell wins: it is the robot's goal.

    The ordering rule in the derivation -- objects first, then locations -- is
    what makes a waypoint that happens to coincide with a perceived object
    reachable.  Here the object is placed exactly on ``kitchen``, which is the
    worst case a live scene can produce.
    """
    document = default_seed_document()
    kitchen = document.locations['kitchen']
    grid = derive_occupancy_grid(
        document.locations,
        [type(document.objects[0])(
            object_id='blocker_1', label='blocker', pose=kitchen,
            graspable=False, held_by=None)],
    )
    assert _cell(grid, kitchen.position.x, kitchen.position.y) == FREE


def test_bounds_cover_the_scene_plus_margin():
    """Grid bounds are every location and object, padded by the margin."""
    document = default_seed_document()
    resolution = 0.1
    margin = 0.5
    grid = occupancy_grid_from_document(
        document, resolution=resolution, margin=margin)

    xs = [pose.position.x for pose in document.locations.values()]
    ys = [pose.position.y for pose in document.locations.values()]
    xs += [obj.pose.position.x for obj in document.objects]
    ys += [obj.pose.position.y for obj in document.objects]

    # The grid's resolution is the requested one snapped to the float32 the
    # wire format carries (see ``derive_occupancy_grid``), so compare against
    # the published value rather than the double input.
    assert math.isclose(grid.info.resolution, resolution, rel_tol=1e-6)
    published = grid.info.resolution
    # The origin is the lower-left corner of the padded extent.
    assert math.isclose(grid.info.origin.position.x, min(xs) - margin, abs_tol=1e-9)
    assert math.isclose(grid.info.origin.position.y, min(ys) - margin, abs_tol=1e-9)
    # The width/height cover the padded extent (ceil, so it never undershoots).
    assert grid.info.width == math.ceil((max(xs) + margin - (min(xs) - margin))
                                        / published)
    assert grid.info.height == math.ceil((max(ys) + margin - (min(ys) - margin))
                                         / published)
    # Every pose lies inside the grid (the acceptance criterion "bounds hold").
    for x in xs:
        assert min(xs) - margin <= x <= max(xs) + margin
    assert len(grid.data) == grid.info.width * grid.info.height


def test_resolution_is_honoured():
    """A coarser resolution yields fewer cells for the same scene."""
    document = default_seed_document()
    fine = occupancy_grid_from_document(document, resolution=0.05)
    coarse = occupancy_grid_from_document(document, resolution=0.2)
    assert fine.info.width > coarse.info.width
    assert fine.info.height > coarse.info.height
    # ``0.2`` survives the float32 snap only approximately (see
    # ``derive_occupancy_grid``), so compare with a relative tolerance.
    assert math.isclose(coarse.info.resolution, 0.2, rel_tol=1e-6)


def test_z_is_ignored_the_map_is_two_dimensional():
    """A pose lifted a metre up projects to the same cell as one on the floor.

    The grid is 2D at ``z = 0`` (RULING 3); a tabletop object 1.15 m up marks
    its own column, and the object's ``z`` must not shift its cell.  The whole
    scene is kept and only ``mug_1``'s ``z`` is changed, so the comparison is
    the derivation's, not a different extent's.
    """
    document = default_seed_document()
    mug = document.find_object('mug_1')
    assert mug is not None
    grounded = occupancy_grid_from_document(document)
    lifted_pose = type(mug.pose)(
        position=type(mug.pose.position)(mug.pose.position.x, mug.pose.position.y, 5.0),
        orientation=mug.pose.orientation,
    )
    lifted_objects = [
        (type(obj)(object_id=obj.object_id, label=obj.label, pose=lifted_pose,
                   graspable=obj.graspable, held_by=obj.held_by)
         if obj.object_id == 'mug_1' else obj)
        for obj in document.objects
    ]
    lifted = derive_occupancy_grid(document.locations, lifted_objects)
    assert lifted.info.width == grounded.info.width
    assert lifted.info.height == grounded.info.height
    assert lifted.info.origin.position.x == grounded.info.origin.position.x
    assert lifted.info.origin.position.y == grounded.info.origin.position.y
    assert _cell(lifted, mug.pose.position.x, mug.pose.position.y) == OCCUPIED


def test_grid_is_in_the_map_frame():
    """The derived grid declares the map frame (RULING 3: map == world)."""
    grid = occupancy_grid_from_document(default_seed_document())
    assert grid.header.frame_id == 'map'
    assert grid.info.origin.orientation.w == 1.0


def test_bad_parameters_are_refused():
    """A non-positive resolution or negative margin is refused loudly."""
    import pytest
    document = default_seed_document()
    with pytest.raises(ValueError):
        occupancy_grid_from_document(document, resolution=0.0)
    with pytest.raises(ValueError):
        occupancy_grid_from_document(document, resolution=-0.05)
    with pytest.raises(ValueError):
        occupancy_grid_from_document(document, margin=-0.1)


def test_zero_footprint_radius_is_a_single_cell():
    """footprint_radius 0 marks only the object's own cell (regression).

    ``_footprint_offsets`` documents that a zero-extent object still marks its
    own cell -- and only that cell.  A previous ``max(radius, 1e-9)`` fudge
    inflated radius 0 to a 3x3 square; the offsets must be exactly ``(0, 0)``.
    """
    from robot_nav.static_map import _footprint_offsets
    assert list(_footprint_offsets(0.0, 0.05)) == [(0, 0)]
    assert list(_footprint_offsets(0.0, 0.2)) == [(0, 0)]
