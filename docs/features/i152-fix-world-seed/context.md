# Context map — issue #152 (fix the world seed so the physical world can be default-on)

Worktree: `/home/sisyphus/worktrees/i152-fix-world-seed`, branch `feat/i152-fix-world-seed`, off
`origin/main` `433525e6` (PR A / #150 merged). Env built (`.pixi/envs/default`); probes below ran with
`source install/local_setup.bash` + `.pixi/envs/default/bin/python` (mujoco 3.12.0).

Labels: **[E]** = empirically-observed (command shown), **[S]** = inferred-from-source (read only).

---

## 0. What is actually wrong (all **[E]**, one settle run)

Seed = `src/robot_world/robot_world/default_world.json` (loaded via
`robot_world.storage.default_seed_document()`, `storage.py:116`, resource `DEFAULT_SEED_RESOURCE =
'default_world.json'` `storage.py:57`). Materialized through the sim's own path and settled 20 000
steps (`.pixi/envs/default/bin/python /tmp/i152_probe3.py`):

```
settle (t=40s): mug_1   seed=(2.10,0.10,1.15) -> (2.07,0.04,1.34)  |Δ|=0.203   (ejected at t≈0.4s to z=3.48)
               plate_1 seed=(2.10,-0.10,1.15)-> (2.00,-0.20,1.31) |Δ|=0.209
               bowl_1  seed=(2.20,0.00,1.15) -> (2.27,0.07,1.33)  |Δ|=0.199
               book_1  seed=(0.10,2.00,0.90) -> (0.22,1.87,-0.00)|Δ|=0.919   (falls to floor)
               cup_1   seed=(0.15,2.00,0.90) -> (0.22,1.98,-0.02)|Δ|=0.919   (falls to floor)
               counter_1 (2.15,0,0.85) static: unchanged; sofa_1 unchanged
```

- **(a) mug/plate/bowl start INSIDE the counter box** → deep penetration → violent ejection. **[E]**
  3-D AABB overlap (probe 2): `mug_1/plate_1/bowl_1` are each fully contained in `counter_1`'s box.
- **(b) book/cup are floating** — bottoms at z 0.885 / 0.855, above the floor (`-0.05`) and clear of
  the support surface's x-range → they fall. **[E]**
- **(c)** the giant support slab + counter block the charger→kitchen path. **[E]** — placing the base
  at (x,0,0) and calling `mj_forward` gives **0 non-floor contacts at x≤1.10**, **1 contact
  (`world_support` ↔ `base_link` mast) from x≈1.12**, and **9–17 contacts (`counter_1` ↔ arms/column,
  `world_support` ↔ `base_link`) at x=1.5 / x=2.0**. So the sim physically cannot reach kitchen.

Also **[E]**: `book_1` and `cup_1` intersect **each other** (book x[0.00,0.20] vs cup x[0.115,0.185]).

---

## 1. Physical-world machinery (#150 / PR A)

### 1.1 `robot_description/world_bodies.py`

- Constants (all **[E]**, printed from the installed module):
  - `SUPPORT_TOP_Z = 1.10` (`world_bodies.py:107`) — chosen so ship-seed `mug_1` (z 1.15, half-height
    0.05) rests exactly on it.
  - `SUPPORT_HALF_EXTENT = 1.0` (`:112`) → **2 m × 2 m** slab.
  - `SUPPORT_HALF_THICKNESS = 0.05` (`:117`) → **10 cm** slab; box centre z = **1.05**, span z[1.00,1.10].
  - `SUPPORT_CENTER = (2.15, 0.0)` (`:133`) → slab spans **x[1.15,3.15], y[-1.00,1.00]**.
  - `SUPPORT_BODY_NAME = 'world_support'` (`:136`).
  - `DEFAULT_BOX_SIZE=(0.05,0.05,0.05)` `:91`, `DEFAULT_MASS=0.3` `:96`, `DEFAULT_FRICTION=(1.0,0.005,0.0001)` `:102`.
- `PHYSICAL_GEOMS_BY_LABEL` (`:168`) mirrors `robot_moveit.scene_geometry.GEOMETRY_BY_LABEL`
  (`scene_geometry.py:58`); box dims are **half**-extents, cylinder dims `(radius, half_height)`.
  Relevant: `mug` CYL(0.04,0.05) `:170`, `cup` CYL(0.035,0.045) `:172`, `plate` CYL(0.12,0.005) `:174`,
  `bowl` CYL(0.08,0.025) `:176`, `book` BOX(0.10,0.07,0.015) `:178`, `counter` BOX(0.30,0.30,0.45)
  `:182` (mass 30 kg), `sofa` BOX(0.90,0.40,0.20) `:184`. `physical_geom_for_label()` (`:200`) falls
  back to a 5 cm box + `DEFAULT_MASS`.
- `collidable_world_bodies_xml(document, *, support_top_z=SUPPORT_TOP_Z, support_center=SUPPORT_CENTER)`
  (`:320`) returns support-body-first, then every object in document order (`:343-345`). **Both are
  kwargs** — a test may place the surface for its own scene without touching module constants. **[S]**
- `_support_body_xml` (`:271`) emits a **static** body (no joint, no inertial), box top at
  `support_top_z`. `_object_body_xml` (`:293`): graspable → `<freejoint>` + `<inertial>` + collidable
  geom (no `contype/conaffinity`, no `gravcomp`); non-graspable → static body, geom only.
- Module docstring (`:117-133`) records the mast constant: **column mast = 0.06 m sq., world z
  0.115…1.445**, i.e. it passes through `SUPPORT_TOP_Z`; that is why a plane was refused and why the
  slab is offset to x=2.15.

### 1.2 Overlap, computed and confirmed

**[E]** AABBs (probe 2, from `PHYSICAL_GEOMS_BY_LABEL`):

| body | x | y | z |
|---|---|---|---|
| `counter_1` | 1.85…2.45 | -0.30…0.30 | 0.40…1.30 |
| `world_support` | 1.15…3.15 | -1.00…1.00 | 1.00…1.10 |
| `mug_1` | 2.06…2.14 | 0.06…0.14 | 1.10…1.20 |
| `plate_1` | 1.98…2.22 | -0.22…0.02 | 1.145…1.155 |
| `bowl_1` | 2.12…2.28 | -0.08…0.08 | 1.125…1.175 |
| `book_1` | 0.00…0.20 | 1.93…2.07 | 0.885…0.915 |
| `cup_1` | 0.115…0.185 | 1.965…2.035 | 0.855…0.945 |

`counter_1 ∩ world_support` = **x[1.85,2.45] × y[-0.30,0.30] × z[1.00,1.10]**. Every kitchen object is
inside `counter_1`. **Note the counter's own bottom is at z=0.40** — it floats 0.40 m above the floor
(it was authored for the *ghost* backend, not physics).

### 1.3 `mjcf_model.py` splice

`write_mjcf_model(path, *, base_free_joint=True, floor=True, world_bodies='')` (`mjcf_model.py:582`).
`FLOOR_BODIES` = plane at `z=-0.05` (`:187`). The call concatenates `FLOOR_BODIES + world_bodies`
(`:618-623`) and passes it to `_build_merged_mjcf` (`:473`) → `_insert_world_bodies` (`:282`), which
re-indents the fragment 1 level and splices it as siblings of the wrapped `base_link` (`:301-309`).
Empirically the derived model has `nbody=53, ngeom=74` and a `base_free` joint. **[E]**

### 1.4 `mujoco.launch.py` gating

- `DEFAULT_MJCF = ~/.ros/sisyphus_derived_scene.xml` (`mujoco.launch.py:80`);
  `DEFAULT_WORLD_STATE = ~/.ros/sisyphus_world.json` (`:86`).
- `_world_bodies_xml(path)` (`:130`) reads the world file as a `WorldDocument` and returns
  `collidable_world_bodies_xml(document)`; any read/parse failure degrades to `''` (robot+floor).
- `DeclareLaunchArgument('world_objects', default_value='false', …)` (`:322-330`).
- `_materialize_world_objects(context, …)` (`:183`) is an `OpaqueFunction` registered as the **first
  executable action** (`:333`); it reads `world_objects` (`:206`) and, if in `('true','1','yes')`,
  calls `_materialize_mjcf_for_state` (`:162`), else `write_mjcf_model(mjcf_path, world_bodies='')`
  (`:212-213`). **R4 = flip the `default_value` at `:323` to `'true'`.**

---

## 2. Robot footprint and arm geometry (empirical, shipped URDF → derived MJCF)

Source facts **[S]**: `base.xacro:44` `wheel_radius=0.05`, `:49` `base_radius=0.125`, `:53`
`wheel_width=0.03`, `:62` `chassis_radius=0.15`, `:63` `chassis_height=0.06`, `:74`
`chassis_z_offset=0.085`; `column.xacro:117-131` rail 0.06×0.06, carriage 0.14×0.10×0.08,
`:109-110` travel 0.00…1.20; `arm.xacro:80-86` `shoulder_offset_y=0.18`, `shoulder_offset_z=0.50`,
`reach_radius=0.85`. `robot_model.load_robot_model()` (`robot_model.py:249`) reads those five
properties out of the shipped xacro (**[E]** — it is what the Mock's `RobotModel` defaults come from,
`mock_world.py:104-115`).

Probe (`.pixi/envs/default/bin/python /tmp/i152_probe.py`), derived MJCF, `qpos=0`, `column_lift=0.30`,
per-geom local-box AABB **[E]**:

```
WHOLE ROBOT (q=0, column 0.30):  x[-0.1500, 0.4914]  y[-0.2933, 0.2935]  z[-0.0500, 1.4450]
  x-extent 0.6414   y-extent 0.5868   z-extent 1.4950   max|x| 0.4914   max|y| 0.2935
base_link        x[-0.150,+0.150] y[-0.150,+0.150] z[+0.055,+1.445]   <-- chassis + FUSED mast
column_top       x[-0.127,+0.158] y[-0.281,+0.281] z[+0.415,+1.166]   (column 0.30)
left_gripper_lower_jaw_link  y[+0.067,+0.194] z[+0.935,+1.084]
left_gripper_upper_jaw_link  y[+0.167,+0.294] z[+0.935,+1.084]
right_gripper_lower_jaw_link y[-0.293,-0.166] z[+0.935,+1.084]
```

- `column_rail_link` is **fused into `base_link`** by MJCF `fusestatic` (the rail joint is fixed), so
  `base_link`'s AABB is chassis (r 0.15, z 0.055…0.115) **plus the mast** (0.06×0.06, z 0.115…1.445).
  Same r/x/y at every column height (the mast is static). **[E]**
- **The numbers the corridor needs:**
  - base-only half-width ≈ **0.15 m** (chassis), wheels reach ≈ 0.149 m.
  - **whole-robot max |y| = 0.2935 m** (arms home) and **max |x| = 0.4914 m** (forward).
  - mast cross-section = 0.06×0.06 (x,y ±0.03), z 0.115…1.445.
  - At column 0.30 the arms sit at **z 0.93…1.20** — i.e. right in the support slab's z-band, so it is
    not only the mast that hits the slab.
- **Datum offset [E]:** `column_top` body origin at column 0.30 is z = **0.4950** =
  `chassis_z_offset + chassis_height/2` (0.115) + 0.08 (carriage) + 0.30. The URDF shoulder frame is
  `column_top + shoulder_offset_z` → shoulder z = **0.995** at column 0.30. The **Mock**'s
  `RobotModel.shoulder` is `column_height + shoulder_offset_z` = **0.80** (`mock_world.py:142-150`) —
  a **0.195 m discrepancy** between the Mock reach model and the URDF/MJCF datum (D31 territory; flag
  it, don't fix it here).

---

## 3. Reachability constraint (PR4 / #149 R6) — what pins object heights

`src/robot_backends/test/test_mock_world.py:35-60`
(`test_the_table_holds_more_than_one_graspable_object_to_clear`): requires **≥ 2** graspable objects
within **`(pose - table_stand).norm() < 1.0`** (a **3-D** norm) of the `table` stand, and each
reachable from a `table` shoulder at `start_column_height` (`reach_radius` 0.85).

**[E]** (probe 2, using `default_world()`):
```
book_1 (0.10,2.00,0.90): 3D-dist to table stand 0.9055  on_table=True   reachable d=0.229 (L and R)
cup_1  (0.15,2.00,0.90): 3D-dist to table stand 0.9124  on_table=True   reachable d=0.255 (L and R)
mug_1/plate_1/bowl_1: 3D-dist ≈ 3.06-3.19 (kitchen cluster, not on the table)
```
**Hard consequence:** the table cluster's z cannot rise above ≈ **0.995 / 0.989** (book dx=0.10, cup
dx=0.15) without falling out of the `< 1.0` window and breaking that `robot_backends` test — which is
out of scope to edit. Raising book/cup to z=1.15 (the kitchen cluster height) would break it.
Shoulder z at seed column height 0.30 = **0.80**; reachable z-band at the stand xy = **[−0.05, 1.65]**;
with the seed xy offsets the band is ≈ [−0.04, 1.64] (mug) — so *reachability itself* is not the
binding limit; R1 (resting) and this 1.0 m window are.

`test_default_seed.py:35-40` (the `EXPECTED_OBJECTS` comment) records the same R6 intent: *"the
graspable objects sit inside the real 5-DOF arm's reachable workspace (a thin z-band above the
shoulder), not at the old table/counter surfaces, which the arm cannot reach at the seed column
height."* The PR4 commit (`327df6b`) diff shows the re-place: mug (2.3,0.1,0.9)→(2.1,0.1,**1.15**),
counter (2.4,0,0.45)→(2.15,0,**0.85**), etc. **[E]**

### Every test that pins a pose / the object or location set

| # | file:line | what it pins | breaks on |
|---|---|---|---|
| 1 | `src/robot_world/test/test_default_seed.py:28-33` | `EXPECTED_LOCATIONS` exact xyz (4) | any location move |
| 2 | `src/robot_world/test/test_default_seed.py:41-49` | `EXPECTED_OBJECTS` exact (label,x,y,z,graspable) ×7 | **any pose move** |
| 3 | `src/robot_world/test/test_default_seed.py:57` | `start_column_height == 0.3` | height change |
| 4 | `src/robot_nav/test/test_static_map.py:43-44` | `EXPECTED_LOCATIONS`/`EXPECTED_FURNITURE` **sets** | add/remove location or furniture |
| 5 | `src/robot_nav/test/test_static_map.py:82-171` | loc cell FREE, furniture+graspable cell OCCUPIED, grid bounds from min/max | moving anything (bounds/dims), set changes |
| 6 | `src/robot_bringup/test/test_nav_localization.py:310-322` | published grid: every location FREE, every non-graspable OCCUPIED | moving/adding furniture, set change |
| 7 | `src/robot_backends/test/test_mock_world.py:35-60` | ≥2 graspables within 1.0 of `table`, each reachable | table-cluster move (z>~0.99), set change |
| 8 | `src/robot_moveit/test/test_world_to_scene.py:28,52-54` | `SEED_OBJECT_IDS` set + document order | object set/order change |
| 9 | `src/robot_moveit/test/test_planning_scene_bridge_e2e.py:47,214-222` | `SEED_OBJECT_IDS` + per-id primitive kind | object set change |
| 10 | `src/robot_backends/test/test_mujoco_backend.py:538-539` | `_PLACE_FIRST=(2.15,0,0.95)`, `_PLACE_SECOND=(2.15,0.15,0.95)` — derived from the counter centre (2.15,0,0.85)+0.10 | if counter moves these no longer sit above it (still valid as raw poses) |
| 11 | `src/robot_mcp/test/test_mcp_mujoco_parity.py:208` | `_COUNTER_DROP=(2.15,0,0.95)` — same derivation, "borrowed from PR4 status.md R6" | same |
| 12 | `src/robot_moveit/test/test_moveit_execution.py:142` | `OUT_OF_REACH_TARGET=(2.10,0.10,1.15)` = mug's seed pose, but asserted as a **number** ("keeps this independent of the seed file") | not a hard pin (no seed read) |
| 13 | `src/robot_brain/test/test_prompt_drift.py:143-145,430-445` | prompt vocabulary / example ids ⊆ `default_world()` objects, locations, labels | id/label/location **rename** only |
| 14 | `src/robot_bringup/test/test_world_launch.py:56` | uses `mug_1` at its **runtime** pose | only if `mug_1` is renamed/removed |
| 15 | `src/robot_bringup/test/test_mujoco_launch.py:148-208` | iterates `document.objects`, asserts each `<body name=…>` + `world_support` present | object removal (pose-agnostic) |
| 16 | `src/robot_bringup/test/test_pr3_navigate.py` | E2E charger→kitchen against the **shipped launch** (no `world_objects` override) → **once R4 flips the default this exercises the materialized world: this is R2** | physical blockage / nav failure |
| 17 | `src/robot_mcp/test/test_clear_the_table.py:12-20,255-310` | chore over `default_world()`; selects nearest objects relative to the robot; keep-out box built from the counter's own pose | mostly robust; still needs ≥2 reachable objects at the table |

Notes: `test_world_to_scene.py:85-89` also pins mug primitive `CYLINDER, [0.10, 0.04]`; the
`robot_skills` golden fixtures use their **own** scene (1.3,0.2,0.9) and are seed-independent;
`scripts/tests/*` contain no coordinate pins. **[S]/[E]**

---

## 4. Nav path and static map

`src/robot_nav/robot_nav/static_map.py`: `FREE=0`, `OCCUPIED=100` (`:84-85`),
`DEFAULT_RESOLUTION=0.05` (`:89`), `DEFAULT_MARGIN=1.0` (`:91`),
**`DEFAULT_FOOTPRINT_RADIUS=0.05`** (`:94`). `_footprint_offsets(radius,res)` (`:97`) yields the
square of half-side `radius` in **cell** units (`span=ceil(radius/res)`); radius 0 → only `(0,0)`.
`derive_occupancy_grid` (`:123`) marks every object's footprint OCCUPIED **first**, then forces each
location's cell FREE (`:204-215`). So **only each object's centre is projected** — the counter's true
0.6×0.6 footprint is *not* in the map; it marks a **3×3 cell** patch (radius 0.05 at res 0.05) around
(2.15,0). The `world_support` slab is **not** a world-document object, so it is never in the map at
all. **[E]**

**[E]** map for the seed (`occupancy_grid_from_document(default_seed_document())`):
`res≈0.05, origin=(-3.0,-1.1), 124×82`. Cell values: every location FREE (charger (21,59), kitchen
(21,99), table (61,59), living_room (41,19)); `counter_1` cell (21,102) OCCUPIED; the graspables'
cells OCCUPIED. The **corridor row y=0, x=0…2.0 is entirely FREE** — the *map* does not block PR3,
the **physics** does (item 0(c)). `test_pr3_navigate.py:79-92` sets `KITCHEN_X=2.0`, `MIN_ARRIVAL_DX=1.0`,
`MAX_ARRIVAL_ABS_DY=0.3`, `MAX_ARRIVAL_ABS_DYAW=0.5`; it launches (`:249-254`) the shipped
`mujoco.launch.py` with **no `world_objects`** argument.

**Clearance the corridor needs [E]:** robot max |y| = **0.2935**; so any static box/slab edge must
satisfy |y_edge| > 0.2935 (plus margin) to stay clear of the driving robot. For the current shapes
this means: support slab centre must move so `|cy| − 1.0 > 0.294` (⇒ |cy| ≳ 1.30) **or** the slab must
shrink; counter must move so `|cy| − 0.30 > 0.294` (⇒ |cy| ≳ 0.60) **or** the counter must shrink.
(Arms are the binding geometry, not the base: the base alone needs only 0.15.)

---

## 5. Out-of-scope boundary

- **`robot_backends/` and `robot_mcp/` must not be touched** (§3 table rows 7,10,11,17 are *observable
  constraints* on the seed, not files to edit).
- `robot_backends/mujoco_backend.py::_world_bodies_xml` (`:228`) is a **separate, non-physical** path:
  bodies carry `gravcomp="1"` and every geom `contype="0" conaffinity="0"` (`:254-266`) — attach-on-close
  bookkeeping for the in-process backend (D34), *not* an obstacle path. It stays unchanged; the
  ROS-sim physical path is `robot_description/world_bodies.py` (`world_bodies.py:17-24`).

---

## 6. Test patterns to follow + the ratchet

- `src/robot_description/test/test_world_bodies.py` — the physics gate for the converter. Its
  `test_seeded_object_rests_at_its_seed_pose` (`:188-215`) is exactly the regression shape for R1: seed
  an object with its bottom on the surface, `SETTLE_STEPS = 20000` (`:106`), assert x/y within 1e-3 and
  center-z within 1e-3. **But** it builds a *synthetic* single-object document (helper `_seed_object`
  `:62`, defaults x/y to `SUPPORT_CENTER`); a new regression test should feed the **shipped seed**
  through `collidable_world_bodies_xml(default_seed_document())` and assert every graspable object
  settles at its seed pose. The contact-pair assertion (`:213-…`) is the pattern for "really resting".
- `src/robot_bringup/test/test_mujoco_launch.py::test_materialized_mjcf_contains_the_world_objects`
  (`:148`) — drives the launch module's `_materialize_mjcf_for_state` value-level helper against the
  seed and greps for `world_support` + each object body; the pattern for R4 (add a case that asserts the
  `world_objects` **default** is now true, e.g. via `_materialize_world_objects` and a fake context).
- `src/robot_bringup/test/test_pr3_navigate.py` — the R2 gate; it **skips** unless
  `mujoco_ros2_control` is installed (`:48-50`, `_have_package`). It does **not** pass `world_objects`,
  so R4's default flip is what makes it run against the materialized world.
- **Ratchet:** `scripts/test_baseline.json` (D28) holds per-package floors of *ran* (non-linter) tests;
  `pixi run test` (`pixi.toml:131` → `scripts/check_test_integrity.py`) **raises** a floor on a green
  run and fails if fewer run. Current floors: `robot_description` 83, `robot_world` 68, `robot_nav` 43,
  `robot_bringup` 19, `robot_backends` 113, `robot_mcp` 102. Adding regression tests therefore means
  committing the raised numbers for whichever packages grow (most likely `robot_description` and/or
  `robot_world`). **Do not lower any floor.**

---

## 7. Open questions for the manager to rule on

1. **One surface or two?** The two clusters sit at different heights (kitchen z=1.15, table z=0.90)
   and `collidable_world_bodies_xml` currently emits exactly **one** support surface
   (`world_bodies.py:343`). Does the fix (i) keep one surface under the kitchen cluster and put the
   table cluster on a *second* surface, (ii) put the table cluster on the floor, or (iii) lower/raise a
   cluster so one surface serves both?
2. **book/cup on the floor vs a second surface** — R3 says keep the scene plausible. Book/cup are
   modelled as tabletop objects; is a floor pose acceptable, or is a second low surface (top ≈ 0.90,
   so book z=0.915 / cup z=0.945, both still `on_table` per the 1.0 m window) required?
3. **Where does the counter + support surface go** so they clear the y=0 corridor (need |y_edge| >
   ~0.294) while the kitchen objects stay reachable from the kitchen stand? Both clusters' surfaces must
   also stay reachable from their stand, and the counter must not intersect any object (R1).
4. **Move the counter, shrink it, or both?** Its half-extents are 0.30×0.30×0.45 at
   `world_bodies.py:182` and mirrored in `scene_geometry.py:73` (a **second** copy the sim/planner
   agreement depends on). Changing the size means changing both tables in lockstep.
5. **May seed object heights change?** R1 requires resting; `plate_1`/`bowl_1` bottoms are 0.045/0.025
   **above** `SUPPORT_TOP_Z`, so they do not rest exactly even once out of the counter. Are their z
   values in scope to adjust (and with them `test_default_seed.py:41-49` and any reach table)?
6. **Fixed reference constants vs the seed.** §3 rows 10-12 encode the counter/mug coordinates as
   literals in `robot_backends`/`robot_mcp`/`robot_moveit` tests that must not be edited. Does the seed
   move *to* those literals (i.e. keep counter x=2.15, z=0.85) or do we accept that those literals
   become stale-but-valid poses?
7. **Does the mast-vs-column `0.195 m` datum discrepancy (§2) matter** for R1/R2 here, or is it an
   accepted D31 follow-up left untouched by this fix?
