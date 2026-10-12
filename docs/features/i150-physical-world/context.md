# i150 physical-world — context map (worktree manager recon)

Issue **#150 MoveIt pick-and-place, PR A — physical world in the sim (collidable
objects + support surface)**. Base: `origin/main` @ `3055083`. Worktree branch
`feat/i150-physical-world` at `/home/sisyphus/worktrees/i150-physical-world`.

This PR is PR A of the three-way PR3 (#149) breakdown. The PR3 implementer found
the MoveIt sim has **no physical world objects** — `mujoco.launch.py` materializes
robot + floor only; world objects exist only as MoveIt planning-scene collision
geometry. This PR builds the physical world.

## 1. The problem (verified)

- `robot_bringup/launch/mujoco.launch.py` materializes the sim MJCF with
  `robot_description.mjcf_model.write_mjcf_model(DEFAULT_MJCF)` — which splices
  only `FLOOR_BODIES` (a floor plane) + the robot trunk (see
  `mjcf_model.py::write_mjcf_model`, `FLOOR_BODIES`).
- World objects are rendered into a sim only by the *in-process* backend
  (`robot_backends/mujoco_backend.py::_world_bodies_xml`), and even there they are
  **deliberately non-physical**: `contype="0" conaffinity="0"` and `gravcomp="1"`
  (attach-on-close bookkeeping, D34). R1 forbids reusing that path.
- `~/.ros/sisyphus_derived_scene.xml` (the live sim model) has 44 bodies, no world
  objects (i149 finding, verified).

## 2. What exists (do NOT rebuild / key touch points)

### The `world_bodies` seam already exists (robot_description)
`mjcf_model.py::_build_merged_mjcf(pkg, world_bodies, base_free_joint)` splices a
`world_bodies` string of `<body>` blocks into the worldbody (via
`_insert_world_bodies`), **after** the free-joint wrap. `write_mjcf_model(path,
base_free_joint=True, floor=True)` currently passes `world_bodies=FLOOR_BODIES if
floor else ''`. This is the seam the new converter plugs into.

`write_mjcf_model`'s drivable path (default) also: injects
`<option integrator="implicit"/>`, adds the rim-roller ring (`_add_rim_rollers`),
and wraps the trunk in a free joint at `pos="0 0 0"`. Floor top surface sits at
world z = -0.05 (one wheel radius below `base_link` origin at z=0).

### World schema (robot_world — ROS-free import, VERIFIED)
`robot_world` (`document.py`) exports `WorldDocument` / `WorldObject`:
- `WorldObject`: `object_id`, `label`, `pose` (`Pose` with `position` x/y/z +
  `orientation` quat), `graspable` (bool), `held_by`.
- `Pose.position` is the object **center**; world/map frame = sim worldbody frame
  (base origin at (0,0,0)).
- `default_seed_document()` ships `default_world.json`. Verified: importing
  `robot_world` loads **no rclpy** (ROS-free), so `robot_description` can
  `exec_depend` on it without breaking the "no ROS import at runtime" discipline
  (robot_backends already does exactly this).
- `WorldDocument.from_dict(json.loads(...))` parses the live-state JSON.

Seed object poses (default_world.json): mug/plate/bowl z=1.15 (graspable),
counter z=0.85 (non-graspable), book/cup z=0.9 (graspable), sofa z=0.4
(non-graspable).

### Geometry source (robot_moveit — read-only reference, do NOT import)
`robot_moveit/scene_geometry.py::GEOMETRY_BY_LABEL` maps label → (BOX|CYLINDER,
dims in metres): BOX dims = **full extents** (x,y,z); CYLINDER dims =
**(radius, height)**. `spec_for_label` falls back to `DEFAULT_BOX_SIZE (0.05³)`.
This is the planning-scene geometry, so the sim's physics geometry must **match
it** (half-extents / half-height in MuJoCo).

**Do NOT import robot_moveit from robot_description**: `robot_moveit_config` →
`robot_description`, and `robot_moveit` → `robot_bringup` → `robot_description`,
so `robot_description` → `robot_moveit` is a dependency cycle.

Note: `robot_backends._PRIMITIVE_GEOMS` **already diverges** from
`GEOMETRY_BY_LABEL` (e.g. mug radius 0.035 vs 0.04). The new converter mirrors
`scene_geometry` (the planning scene), **not** robot_backends.

## 3. Physics probe (VERIFIED — manager ran it)

A free-jointed cylinder (mass 0.3, size r=0.04 half-h=0.05) with a `<freejoint>`
+ `<inertial>` + collidable `<geom>` over a `plane` floor, implicit integrator:
- settles to z≈0.0499 (bottom on floor at z=0) — **no fall-through**;
- `xfrc_applied` push moves it (x 0 → 1.46) — **real contact response**; no NaN.

So the core physics (rest + contact) works in MuJoCo 3.12 with the implicit
integrator. The object's `pos` z is its center; it rests with its bottom on the
surface below it.

## 4. Test patterns to mirror

- `robot_description/test/test_mjcf_model.py` — in-process: `load_mjcf_model()`,
  `MjData`, `mj_step`, assert on `nq/nv/nbody/neq`, `xpos`, name lookups. This is
  where the rest + contact-respond assertions belong (deterministic, no ROS).
- `robot_bringup/test/test_mujoco_launch.py` — structural
  `test_launch_generates_expected_nodes` (imports the launch module, walks
  entities) + a heavy conditional e2e smoke (skips if `mujoco_ros2_control`
  missing). The launch-wiring assertion belongs here (cheap, structural).

## 5. Owned paths / out of scope

- **Owned:** `src/robot_description/**` (new converter module + `write_mjcf_model`
  seam extension + tests + package.xml `robot_world` dep), `src/robot_bringup/**`
  (`mujoco.launch.py` wiring + a structural test).
- **Out of scope (do NOT touch):** `robot_backends/`, `robot_mcp/` (D30),
  `robot_moveit/`, `robot_moveit_config/`, `robot_world/` (the seed + store are
  not to be modified). The shipped seed's *physical* incoherence (mug center at
  z=1.15 sits inside the counter box spanning z 0.40–1.30) is a known follow-up,
  not this PR's job.

## 6. Known gotchas

- MuJoCo geom `size` is **half**-extents (box) / (radius, **half**-height)
  (cylinder); MoveIt dims are full extents / full height. Translate.
- A free-jointed body needs `mass` > `mjMINVAL` or compile is rejected (the
  `_wrap_base_freejoint` pattern).
- An **infinite** support `plane` at counter height would intersect the robot
  column/arms — use a **finite box** table instead.
- `pixi run test` ratchets per-package counts in `scripts/test_baseline.json`
  (robot_description=73, robot_bringup=18 now); adding tests auto-raises the
  floor — commit `scripts/test_baseline.json`.
- The build gate: `pixi run build` installs to the `install/` share dir; tests
  read the **installed** share dir, so build before test.
