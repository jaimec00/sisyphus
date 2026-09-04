# PR1 implementation — MuJoCoBackend skeleton + scene loading (issue #99)

Status: implementation complete, scoped-authoritative run green. Written by PR1
implementer #2 (2026-09-04).

## What this PR delivers

1. A **scene-aware compile seam** in `robot_description/mjcf_model.py` —
   `load_mjcf_model_with_scene(world_bodies_xml)` — that reuses the PR7 merge
   machinery and welds caller-supplied static world bodies into the MJCF before
   compilation.
2. **`robot_backends/robot_backends/mujoco_backend.py`** — `MuJoCoBackend
   (RobotBackend)` (D34, in-process `mujoco`), reporting the seed scene through
   the shared `Observation` wire shape.
3. **`robot_mcp --backend mock|mujoco`** (R-7) with `$ROBOT_BACKEND` fallback,
   threaded through `backend_from_options` / `main`.
4. Tests: backend scene acceptance + MCP wire-schema parity.

Behaviour notes: PR1 ships **no skills** — `execute()` refuses every skill with
`FailureCode.UNSUPPORTED_SKILL` (the ABC's total contract). Grasp/place/IK and
true arm kinematics land in PR2; primitive scene geoms are placeholder visuals
roadmap #4 will tune.

---

## 1. Scene-insertion mechanism and the assembly seam (Q-1)

A compiled `MjModel` cannot gain bodies post-compile, so world objects are
spliced into the MJCF **before** compilation, *reusing* the PR7 assembly rather
than re-deriving the robot.

The PR7 assembly is one function, `_build_merged_mjcf(pkg, world_bodies='')`,
which:

1. imports the URDF (mesh assets keyed by lowercase basename),
2. redirects `package://` mesh URIs to the absolute `meshes/` path,
3. splices the hand-authored `overlay.xml` (`_splice_overlay`),
4. **new**: `_insert_world_bodies()` — if a `world_bodies` fragment was passed,
   it is spliced in as the *last sibling inside the single `</worldbody>`*,
   indented one level.

The robot derivation (1–3) is untouched and shared by both entry points;
`load_mjcf_model()` (zero args, unchanged public contract + behaviour) now
delegates to `load_mjcf_model_with_scene('')`, and `write_mjcf_model()` still
calls `_build_merged_mjcf(_package_dir())` with no scene.

**Why insert before `</worldbody>`:** the derived MJCF from the URDF keeps the
*robot's whole tree* inside one `<worldbody>...</worldbody>`, and the overlay
blocks (`<default>`/`<actuator>`/`<sensor>`) live *after* that element. MJCF
requires bodies to be inside `<worldbody>`, which must precede `<actuator>` /
`<sensor>` — so appending raw `<body>` before `</mujoco>` (after the overlay)
would be a schema violation. Splicing just before `</worldbody>` puts scene
bodies right where they belong: immovable siblings of the robot inside the world
body.

**How a world object becomes a static body:** each seed object becomes one body
named `object_id` with a primitive `<geom>` child and **no joint**, at its seed
`Pose.position`. A joint-less body welded to the world never receives dynamics —
no gravity collapse, no contact displacement (also see Q-4, `contype=0`) — so its
world frame equals its seed pose exactly for any number of `mj_step`s. This is
what makes "object poses == seed" an easy, honest acceptance. `graspable` is
*not* modelled as physics in PR1 (static bodies, no joints, contact off); it
survives only as the flag the Observation reports, read from the source world
document. `_world_bodies_xml(document)` in the backend renders the fragment
(labels, poses); `robot_description` stays a generic splice and never learns
scene semantics.

### Frame mapping (R-2)

The fused robot base sits at the world origin, which *is* the seed map's
`start_location` (`charger`, (0,0,0)); MJCF +z == map +z. So the overlay is the
identity: the map frame and the MuJoCo world frame are the same frame. An
object's seed `position` maps 1:1 onto `<body pos=...>`, and `get_observation`
reads the body's `xpos`/`xquat` verbatim as the map coordinate. `known_locations`
is just the (sorted) names from the world document — locations need no marker
bodies because `Observation` reports location *names* (`robot.location`,
`known_locations`), never location poses.

## 2. MuJoCoBackend behaviour

### reset() / get_observation() (R-3, R-5, R-6)

`reset()` re-homes the robot (the scene is welded and never rebuilt):

- wheels → velocity ctrl 0 (the fused base can't drive);
- `column_lift` → qpos = `start_column_height` (0.3) **and** its position
  actuator ctrl = 0.3, so the servo holds it across steps (Q-2: ~0.29989 after
  200 steps);
- arms + grippers (+ mirrors) → joint zero with the position-actuator ctrl 0 sweep
  that is scoped to NON-wheel, NON-column actuators so it must never overwrite
  the column's 0.3 ctrl above (column is the one home joint whose actuator keeps
  a non-zero target — red-team F11);
- `mj_forward`, then observe.

`get_observation()` calls `mj_forward` first, then reports:

- `RobotState.pose` = the map's `start_location` pose (identity at charger) — the
  base is fused/fixed so it never leaves;
- `column_height` = the live `qpos[column_lift]` slot (real sim value);
- both `SIDE_ORDER` grippers: `OPEN`, `held_object_id=None`, `grasped=False`
  (Mock-reset shape, Q-3); reported `pose` is the world-frame midpoint of the two
  open jaw bodies (upper/lower `{side}_gripper_*_jaw_link`) with the upper jaw's
  orientation, a PR1 placeholder for the grasp centre;
- `objects` = sorted seed objects with pose read from each welded body, plus the
  seed's label and graspable flag; `known_locations` sorted; all under the
  `Observation` schema (identical shape to Mock).

### execute() (interface contract)

`execute` raises `TypeError` for a non-`Skill` (programming error) and otherwise
returns a failed `SkillResult` (`UNSUPPORTED_SKILL`, `is_backend_refusal` True)
with an unchanged observation — never raises for a legal `Skill`. PR2 replaces
this with real handlers.

## 3. robot_mcp --backend (R-7)

- `parse_args` gains `--backend {mock,mujoco}` with `$ROBOT_BACKEND` fallback.
  Default (`None`) is Mock — the historical behaviour is unchanged.
- `backend_from_options(world_state, world_seed, backend=None)`:
  - `mock`/`None` → exactly today's behaviour (`None` = let `build_server` make an
    in-memory Mock; a `world_state` makes a file-backed Mock);
  - `mujoco` → a `MuJoCoBackend` compiled from `world_seed` (a world document
    file) or the shipped apartment when no seed is given; never writes a file.
- Option validation: `--backend mujoco` may run with no `--world-state` and may
  supply `--world-seed` alone (the seed *is* the scene). `--world-state` with
  `--backend mujoco` is refused (the in-process sim keeps no live-state file — it
  would silently do nothing). `--world-seed` alone with the default/mock backend
  stays refused, matching the existing `test_world_state_options` behaviour.

## 4. Tests run + results

Authoritative scoped driver (the source of truth for "passed under colcon"):

```
pixi run -- python scripts/check_test_integrity.py --packages-select robot_backends robot_mcp
```

Result: **175 tests, 0 errors, 0 failures, 0 skipped, AUDIT PASSED**.
`test_no_ros_runtime` green in both packages. Test-count baselines rose
(robot_backends 77→84, robot_mcp 82→85) and were committed with the PR.

New tests:
- `robot_backends/test/test_mujoco_backend.py` — is a `RobotBackend`;
  observation objects/location vocabulary == seed within tolerance; reset returns
  robot at charger with column 0.3 and both grippers presenting the Mock-reset
  shape; **a real 200× `mj_step` leaves every object pose exactly at its seed**
  (acceptance); `execute` refuses every skill (`UNSUPPORTED_SKILL`) leaving the
  world unchanged; raises `TypeError` for a non-Skill.
- `robot_mcp/test/test_mcp_mujoco_parity.py` — drives `MuJoCoBackend` through the
  real MCP client (`connected`) and proves reset/observation and the
  `navigate_to` refusal serialise as dicts the shared `Observation` /
  `SkillResult` schema parses and round-trips, and that MuJoCo agrees with a Mock
  reference on every seed-guaranteed field (objects, location, column (==0.3
  after driver-run sanity), map vocabulary, gripper open/empty) while gripper
  poses (sim-derived) are allowed to differ.

Manual dynamic probe (validation before writing tests): compile + 200 `mj_step`s
kept `mug_1` (2.3,0.1,0.9) and `counter_1` (2.4,0,0.45) xpos bit-identical to the
seed.

## 5. What PR5 (and later PRs) must change

- **Grasp/IK (PR2 onward).** Real skins over `load_mjcf_model_with_scene` feed a
  planning/IK layer; `execute` grows real handlers; `_gripper_pose` / gripper-jaw
  midpoint should give way to true commanded grasps when motion arrives.
- **Scene objects become dynamic (PR5 grasp/place).** PR5 must replace these
  static welded scene bodies with **free-jointed bodies resting on a modelled
  floor/surface** so they can be picked up. The seam is ready: it's one per-object
  `<body>` fragment from `_world_bodies_xml`; PR5 re-renders that fragment with a
  `<joint>`/inertial/freejoint instead of a weld and, where grasp matters, gives
  `contype/conaffinity` back their contact role. `graspable`-vs-furniture is
  already available from the world document to drive that split.
- **Visuals (roadmap #4).** `_PRIMITIVE_GEOMS` / `_label_geom` are placeholder
  primitive morphologies (units are MuJoCo half-extents). Replace or augment with
  real meshes/sites then; body-frame reporting is unaffected.
- **Locations as poses.** `Observation` only carries location *names* today; if a
  later feature needs to read a location pose back from the sim it will need a
  world transform / marker bodies — out of PR1 scope (identity overlay means map
  poses are already trivially Map coords).

## Open/unverified notes for red-team

- Arm/gripper **joint posture is not statically controlled** in PR1: position
  servos with ctrl 0 are a weak (kp=100) spring vs. gravity, so after enough
  `mj_step`s the arms drift a little from joint zero (observed ~0.03–0.13 rad over
  200 steps). The R-3 column, at the seed height with matching ctrl, holds to
  ~1e-3 (verified). Scene objects are unaffected (welded & contact-off). If a
  future acceptance steps long *and then reads gripper poses*, home-posture drift
  must be re-homed or gains tuned in PR2 — not here.
- `step(n)` is a public-but-not-interface helper (advances the model), used only
  by the acceptance test.
- Gripper reported pose choice (jaw midpoint, upper-jaw orientation) is a PR1
  placeholder approximation; its exact value is not asserted against Mock (only
  shape). Confirm that's the intended contract before PR2 assumes otherwise.
