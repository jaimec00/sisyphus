# i150 physical-world — manager status

Issue #150 (MoveIt pick-and-place, PR A — physical world in the sim). Worktree
`feat/i150-physical-world` @ `origin/main` `3055083`.
Context map: `docs/features/i150-physical-world/context.md`.

## Rulings (binding; challenge these, don't trust them)

- **R1 (converter location):** New module
  `src/robot_description/robot_description/world_bodies.py` exporting
  `collidable_world_bodies_xml(document, *, support_top_z=SUPPORT_TOP_Z) -> str`.
  Do NOT touch `robot_backends` / `robot_mcp` (D30), and do NOT reuse
  `_world_bodies_xml` / `_PRIMITIVE_GEOMS`.

- **R2 (single source of truth):** the converter's input is a
  `robot_world.WorldDocument` (poses + labels from the D35 world-state seam).
  Add `<exec_depend>robot_world</exec_depend>` to `robot_description/package.xml`
  (import is ROS-free — VERIFIED). No second home for scene data.

- **R3 (geometry table):** a NEW `PHYSICAL_GEOMS_BY_LABEL` table in the converter,
  keyed by label. Shape+dims MUST mirror `robot_moveit.scene_geometry.GEOMETRY_BY_LABEL`
  exactly (BOX full extents → MuJoCo half-extents; CYLINDER (r, height) → MuJoCo
  (r, half-height)). Add per-label estimated mass + a friction value. Unknown
  label → 0.05 m box fallback + default mass (mirror `DEFAULT_BOX_SIZE`). This is
  a known third copy of the label→shape table; dedup is a follow-up (the issue's
  R1 already says "dedup later"). Mirror `scene_geometry`, **not** robot_backends
  (which already diverges: mug radius 0.035 vs 0.04).

- **R4 (real physics):**
  - **graspable** → free-jointed body (`<freejoint name="<id>_free"/>`) + `<inertial>`
    (mass + `diaginertia` **derived from mass + dims** via solid-box/cylinder
    formulas, so a retune can't leave a stale tensor) + `<geom>` with **default**
    contype/conaffinity (i.e. do NOT write `contype="0"`/`conaffinity="0"`), a
    friction value, **no `gravcomp`**.
  - **non-graspable** → static body (no joint, no inertial) + collidable geom.
  - Mass > `mjMINVAL`. Inertia derived from mass + shape.

- **R5 (support surface):** the converter also emits a **finite** static box
  ("table") — NOT an infinite plane (a plane at counter height would intersect
  the robot column/arms). Top surface at `support_top_z` (default `SUPPORT_TOP_Z
  = 1.10` m; chosen so the shipped seed's `mug_1` at z=1.15 with half-height 0.05
  rests exactly). Large top area (e.g. 2 m × 2 m), thin height. A static body:
  no joint, no inertial, collidable geom. Horizontal center + size are converter
  constants/params; the test places its seeded object directly above the surface
  center at `pose.z = support_top_z + half_extent`.

- **R6 (launch wiring):** `robot_bringup/launch/mujoco.launch.py` reads the world
  file (`DEFAULT_WORLD_STATE`), parses it via
  `robot_world.WorldDocument.from_dict(json.loads(...))`, calls
  `collidable_world_bodies_xml`, and materializes the sim MJCF with those bodies.
  Extend `write_mjcf_model` with a `world_bodies: str = ''` keyword that is
  appended **after** `FLOOR_BODIES` (so the sim keeps the drive floor AND gains
  the support surface + objects). The materialized `DEFAULT_MJCF` must then
  contain the world-object `<body>` blocks.

- **R7 (test split):**
  - `robot_description/test/test_world_bodies.py` (in-process MuJoCo, mirror
    `test_mjcf_model.py`): seed a `WorldDocument` with one graspable object at
    `support_top_z + half_extent`; compile the converter output (via the same
    path the sim uses); assert (a) the XML carries a freejoint + inertial + geom
    with NO `contype="0"`/`gravcomp`; (b) after settling the object **rests at its
    pose** (center z ≈ seed z, x/y unchanged — no fall-through/drift); (c) an
    applied perturbation (`xfrc_applied`) **moves** it (contact response).
  - `robot_bringup` structural test (mirror `test_launch_generates_expected_nodes`):
    the launch/materialize path produces a derived-scene MJCF containing the world
    object body names (grep). Keep it cheap; the physics proof lives in
    robot_description.
  - Commit `scripts/test_baseline.json` (auto-raised by `pixi run test`).

- **R8 (out of scope):** do NOT touch `robot_backends`, `robot_mcp`, `robot_moveit`,
  `robot_moveit_config`, or `robot_world` (seed/store). The shipped seed's physical
  incoherence (mug center inside the counter box, etc.) is a follow-up; PR A only
  guarantees the *test's* seeded object rests.

## Ruling amendment (post-implementer)

- **R5a (support centre — AMENDED):** the implementer VERIFIED that R5's finite
  box centred on the robot origin at `SUPPORT_TOP_Z` **does** pierce the column
  mast (a 0.06 m capsule on `base_link` spanning world z 0.115→1.445), exactly
  as a plane would — finiteness bounds the edge, not the footprint. An object
  on an origin-centred surface is ejected at ~120 m/s in the full model.
  **Accepted fix:** `SUPPORT_CENTER = (2.15, 0.0)` (offset clear of the mast,
  under the shipped seed's kitchen cluster), still a keyword parameter. The
  tests assert the footprint does not cover the origin and has no robot contact
  at home. This is the correct reading of R5; no further change needed.

- **Follow-ups (NOTE, not blockers — will file on the issue after PR):** (a) the
  shipped seed's physical incoherence (mug/plate/bowl centres inside the static
  counter box; book/cup off the 2 m surface) is now a live symptom once objects
  are physical — out of scope for PR A but a real follow-up; (b) three copies of
  the label→shape table (scene_geometry, robot_backends, world_bodies) — dedup
  is the follow-up R1 already names.

## Regression + resolution (manager, post-test-runner)

- **REGRESSION (VERIFIED):** materializing the shipped seed's physical objects
  breaks `robot_bringup.test_pr3_navigate` (Nav2 base drive to kitchen). On
  origin/main (i149 worktree, has mujoco_ros2_control + old launch) it PASSES
  (1 passed); in this worktree it FAILS with `NavigateToLocation('kitchen') …
  the semantic goal did not finish in time`. Root cause (probed in MuJoCo): the
  static `counter_1` box (z 0.40–1.30) and the finite support surface
  (z 1.00–1.10), both near the kitchen at (2.15, 0), collide with the robot's
  arms/column when the base drives charger(0,0)→kitchen(2,0). This is the seed's
  physical incoherence (objects AT navigation locations) — R8's follow-up, not
  this PR's job to fix.

- **R9 (RESOLUTION):** world-object materialization is **opt-in** behind a
  `world_objects` launch argument (default `false`). Default `false` preserves
  the pre-#150 sim (robot + floor only) so navigation stays green;
  `world_objects:=true` materializes the collidable world (support surface +
  objects) for the pick-and-place path. The materialization is deferred to an
  OpaqueFunction (it previously ran at description-build time, before the launch
  context, so a LaunchConfiguration could not be read). The converter + physics
  (the actual acceptance) is unaffected and fully tested in-process; PR B/C
  enables `world_objects:=true` after the seed is fixed.

## Falsifiable claims for red-team (disprove empirically — run, don't reason)
1. The converter output compiles in the FULL derived model (rim rollers + implicit
   integrator) and a seeded graspable object settles to its seed pose (center z
   unchanged within tolerance, x/y unchanged) — no NaN, no fall-through, no drift.
2. A real contact perturbation (`xfrc_applied`, or a second body colliding) shifts
   the object — proving contype/conaffinity are actually enabled (not 0).
3. The inertia tensor matches the solid-shape formula for the declared mass+dims
   (recompute by hand; a stale/zero diaginertia is a BLOCK).
4. The support surface is finite and does NOT intersect the robot trunk at its
   home config (no spurious contact at reset).
5. MoveIt planning-scene geometry (`scene_geometry.GEOMETRY_BY_LABEL`) and the new
   converter's geometry agree for every label they both know (half/full extent
   translation is correct).
6. The launch actually materializes the world objects (derived scene XML contains
   the body names) and does not crash at description-build time.

## Loop
- [x] context (context.md)
- [x] rulings (this file)
- [ ] implementer
- [ ] red-team
- [ ] test-runner
- [ ] PR
