# i150 physical-world — implementation (PR A of #150)

Issue **#150 MoveIt pick-and-place, PR A — physical world in the sim**. Branch
`feat/i150-physical-world` off `origin/main` @ `3055083`. Rulings: `status.md`
R1–R8. Map: `context.md`.

## What this PR does

Before this PR the MuJoCo sim had **no world objects**: `mujoco.launch.py`
materialized robot + floor only, and world objects existed solely as MoveIt
planning-scene collision geometry. This PR materializes the D23/D35 world state
as *physical* MuJoCo bodies -- with mass, derived inertia and real contacts --
plus a finite support surface for them to rest on.

## Files

- **NEW** `src/robot_description/robot_description/world_bodies.py` — the
  converter. `collidable_world_bodies_xml(document, *, support_top_z,
  support_center) -> str` emits the worldbody fragment; `PHYSICAL_GEOMS_BY_LABEL`
  is the physics mirror of `robot_moveit.scene_geometry.GEOMETRY_BY_LABEL`; the
  finite-box support surface (R5) and the per-label shape/mass/inertia/friction
  helpers.
- `src/robot_description/robot_description/mjcf_model.py` — `write_mjcf_model`
  gains `world_bodies: str = ''`, appended **after** `FLOOR_BODIES` (R6). The
  default `''` leaves the floor-only path byte-identical.
- `src/robot_description/package.xml` — `<exec_depend>robot_world</exec_depend>`
  (R2; the import is ROS-free).
- `src/robot_bringup/launch/mujoco.launch.py` — reads the world-state file,
  parses `WorldDocument.from_dict(json.loads(...))`, calls the converter, and
  materializes `DEFAULT_MJCF` with the result (R6). A missing/unreadable/
  malformed world file degrades to robot + floor only (never crashes
  description-build). **Since the regression fix below, this is gated on the
  `world_objects` launch argument (default `false`): the default materializes
  robot + floor only, `world_objects:=true` materializes the collidable world.**
  Materialization is deferred to an `OpaqueFunction` (`_materialize_world_objects`)
  so it can read the resolved `world_objects`/`mjcf_path`/`world_state_path`
  values; the value-level helpers `_world_bodies_xml` /
  `_materialize_mjcf_for_state` are kept intact for the structural test.
- **NEW** `src/robot_description/test/test_world_bodies.py` — 10 in-process
  physics tests (R7).
- `src/robot_bringup/test/test_mujoco_launch.py` — one added structural test
  that the materialize path produces the world bodies (R7).
- `scripts/test_baseline.json` — auto-raised by `pixi run test`.

## Regression fix — `world_objects` opt-in default (manager-verified regression)

Materializing the world objects by **default** broke
`robot_bringup.test.test_pr3_navigate::test_navigate_to_location_drives_the_base_and_updates_the_world`:
`NavigateToLocation('kitchen')` reported *"the semantic goal did not finish in
time"*. On `origin/main` (old launch, no world materialization) it passed.

**Root cause:** the shipped seed is *physically incoherent* — it places static
objects **at the navigation locations**. Near the kitchen the static `counter_1`
box (z 0.40–1.30) and the finite support surface (z 1.00–1.10) sit at
`(2.15, 0)`, so when the base drives from the charger `(0, 0)` to kitchen
`(2, 0)` they collide with the robot's arms/column and block navigation. The
seed geometry is a **known follow-up (R8)**, not this PR's job to fix.

**Fix (in-scope, `robot_bringup` only):** gate the materialization behind
`DeclareLaunchArgument('world_objects', default_value='false')`:

- default `false` → `write_mjcf_model(mjcf_path, world_bodies='')`, i.e. the
  pre-#150 robot + floor model that keeps navigation working;
- `world_objects:=true` → the collidable world (robot + floor + support surface
  + objects), for the pick-and-place path.

Because the old code materialized at **description-build time** (before the
launch context / `LaunchConfiguration` existed), the materialization moved into
an `OpaqueFunction` (`_materialize_world_objects`) declared as the first
executable action; it reads the resolved
`world_objects`/`mjcf_path`/`world_state_path`. A side benefit: a
`mjcf_path`/`world_state_path` override now actually applies to the sim model
(it previously could not). The value-level helpers `_world_bodies_xml` and
`_materialize_mjcf_for_state` are unchanged, so the structural test
(`test_materialized_mjcf_contains_the_world_objects`), which drives
`_materialize_mjcf_for_state` directly, still finds the world bodies.

**Verification (this pass):** `test_pr3_navigate` **passes** (1 passed, 41.27 s);
the structural world-object test **passes**; `robot_description`
`test_world_bodies.py` **passes** (10 passed); `pixi run build` clean.
Once the seed geometry is made coherent (R8 follow-up), PR B/C can wire the
pick-and-place path to pass `world_objects:=true` (or flip the default).

## Design

### The seam
`write_mjcf_model(..., world_bodies=...)` → `_build_merged_mjcf` →
`_insert_world_bodies`, which splices the fragment as the last siblings inside
`</worldbody>` (the existing PR1 seam). The surface + objects are spliced **after**
`FLOOR_BODIES`, so the sim keeps the drive floor at z=-0.05 **and** gains the
support surface + objects.

### Physical geoms (R3/R4)
- Geometry mirrors `scene_geometry`: BOX full extents → MuJoCo **half**-extents;
  CYLINDER `(r, height)` → `(r, half-height)`. A test transcribes every mirrored
  label and asserts the half/full translation.
- `diaginertia` is **derived** from mass + dims via solid-box / solid-cylinder
  formulas (`_box_diaginertia` / `_cylinder_diaginertia`), mirroring the
  `_roller_body_xml` pattern — a retune cannot leave a stale tensor. A test
  recomputes both by hand.
- **graspable** → free-jointed body (`<freejoint name="<id>_free"/>`) +
  `<inertial>` + a collidable `<geom>` with default contype/conaffinity and **no
  `gravcomp`**. **non-graspable** → static body (no joint, no inertial).
- Pose quaternion is reordered from `robot_skills` `(x,y,z,w)` to MJCF
  `(w,x,y,z)` — a silent transpose would aim every object wrongly.
- Unknown label → the same 5 cm box `scene_geometry.DEFAULT_BOX_SIZE` uses.

### The support surface — and why the centre is **not** the origin (R5, challenged)
R5 asked for a finite box support surface with its top at `SUPPORT_TOP_Z=1.10`
(chosen so the shipped seed's `mug_1` at z=1.15, half-height 0.05, rests exactly),
"e.g. 2 m × 2 m", with the horizontal centre a converter constant/param.

R5's stated motivation for a **finite box** over a plane is that "a plane at
counter height would intersect the robot's column/arms". A finite box does **not**
avoid that. VERIFIED: the robot's column mast is a 0.06 m sq. capsule on
`base_link` spanning world z 0.115 → 1.445 — straight through `SUPPORT_TOP_Z`.
A surface whose footprint covers the world origin (where the mast stands at
home) therefore pierces the mast regardless of finiteness. Compiled in the full
sim path (robot + rollers + implicit integrator), an object seeded on an
origin-centred surface is ejected at ~120 m/s.

So `SUPPORT_CENTER = (2.15, 0.0)` — offset **clear of the mast**, under the
shipped seed's graspable kitchen cluster (`mug_1` at `(2.1, 0.1, 1.15)`), which
is also what `SUPPORT_TOP_Z` was chosen for. `support_center` is a keyword
(after `support_top_z`), so a different scene can place its own surface. The
test suite asserts (a) the footprint does not cover the origin and (b) at home
the surface has **no** contact with the robot trunk.

## Test results

`pixi run build` clean; `pixi run test` green (see `scripts/test_baseline.json`
counts). The physics tests (in-process MuJoCo, the sim's own materialize path):
(a) graspable block is a free joint + inertial + default-contype geom, no
`contype="0"`/`gravcomp`; (b) the derived inertia matches the hand-computed
solid-shape formula; (c) a seeded object **rests at its seed pose** (center z
within 1 mm, x/y within 1 mm, no NaN) after 20 000 steps; (d) an `xfrc_applied`
push moves it (real contact response); (e) the surface is a finite box with its
top at `SUPPORT_TOP_Z` and clear of the mast; (f) geometry mirrors
`scene_geometry` for every shared label.

**Red-team fix pass (BLOCK-1 + NOTE-1).** (d) alone did not prove contact:
`xfrc_applied` is a direct DOF force, applied even to a collision-inert body, so
the push passed on a `contype=0` ghost that free-falls through the surface. It
now also asserts the object's geom forms a resting contact pair with
`world_support_geom` in `data.contact[:data.ncon]` — VERIFIED sensitive: present
on the real model, absent (`ncon=0`, object at z≈−77) with `contype="0"`
injected. The push is kept, honestly labelled as proving only movability. NOTE-1:
the rest-test tolerance was tightened 1e-2 → **1e-3** (x/y `atol` and z), so a
~5 mm fall-through can no longer slip through; real margin is ~2e-5 m, and 3
consecutive full-file runs stayed green at 1e-3 (no relaxation needed).

## Challenges / follow-ups for the manager

1. **R5 geometry is wrong (BLOCK-level, corrected in code).** See above: a
   finite box centred on the robot origin at counter height *does* intersect the
   column mast, exactly as a plane would. I kept R5's mechanism (finite box,
   param centre, `SUPPORT_TOP_Z`) but offset the default centre clear of the
   mast. If the manager prefers a different default centre, it is one constant.
2. **R8 seed incoherence now has a physical symptom.** In the shipped seed,
   `mug_1`/`plate_1`/`bowl_1` (z≈1.15, x≈2.1) sit **inside** the static
   `counter_1` box (spans z 0.40–1.30) and are ejected by it, and `book_1`/
   `cup_1` (x≈0.1) are off the 2 m surface. That is R8's known, out-of-scope
   incoherence; PR A only guarantees the *test's* object rests. Worth a
   follow-up: the seed geometry and a per-scene surface.
3. **Three copies of the label→shape table** (`scene_geometry`,
   `robot_backends._PRIMITIVE_GEOMS`, and now `PHYSICAL_GEOMS_BY_LABEL`).
   `robot_description` cannot import `robot_moveit` (cycle), so the mirror is
   unavoidable; dedup remains the follow-up R1 already names.
