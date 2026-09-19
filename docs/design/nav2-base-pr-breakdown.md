# Roadmap — Nav2 base navigation (PROJECT.md step 5, first half): PR breakdown

**Status:** not started. Recorded 2026-09-19 (D36). This is the base-navigation
half of PROJECT.md step 5 (classical skills first, D22): replace the
semantic-map stub `navigate_to` (today the backend just teleports the base) with
a real Nav2 navigation stack that actually drives the holonomic LeKiwi base
through the PR8b `dfki-ric/mujoco_ros2_control` control stack.

**Decided (D36):** Nav2 runs on the **classical/real track** — the PR8b ROS 2
control stack (D33) — *not* on the ROS-free brain backend (D34). The brain's
tool surface (`navigate_to(location)`) is unchanged; the named-location → pose
resolution it already has (`robot_world`'s `locations` reference poses, D23) is
what Nav2 consumes. Nav2 is **on the RoboStack channel** (`ros-jazzy-navigation2`
v1.3.12 verified present) — pixi deps, no source-build. **Sim-first localization
is ground-truth**: a ground-truth `odom → base_footprint` publisher plus a
static map derived from the world store's named locations; AMCL/scan-based
localization is deferred to real hardware (nothing produces a scan today).

## Ground truth this must preserve
- The skill-API seam (D9/D21): `navigate_to(location)` keeps its signature and
  its `SkillResult` shape; the brain never sees raw cmd_vel or poses.
- `robot_world` stays the single source of truth for named locations (D23); the
  map is derived from it, not a second home for location data.
- The safety layer (D17) still clamps/rejects illegal commands — a Nav2 goal
  that exceeds base limits is rejected below the tool boundary.
- D30 boundary held: `robot_backends`/`robot_mcp` stay ROS-free at runtime.
  Nav2 lives in ROS 2 packages, not in the brain's backend.

## The PRs

### PR1 — Nav2 deps + ground-truth localization (odom + static map)
- Add Nav2 to `pixi.toml` (RoboStack `ros-jazzy-navigation2`, `nav2-bringup`,
  `nav2-msgs`, and whatever the bringup needs) and confirm the full env still
  builds.
- Stand up the localization layer Nav2 requires: a ground-truth
  `odom → base_footprint` transform from the sim's base pose, and a static map
  derived from `robot_world`'s named locations. Complete the TF tree
  `map → odom → base_footprint → base_link → wheels`.
- **Probe the real API first** (the open design question): where does the
  dfki-ric sim expose the floating base's pose? (`joint_state_broadcaster` only
  publishes actuated joints; the base is a floating body.) Determine whether
  odometry comes from the sim's base-body pose, from integrating wheel
  velocities, or from a small ground-truth publisher — and pin it in `status.md`.
- **Test:** Nav2 lifecycle nodes start headless; the TF tree is complete and
  connected; localization holds the robot at a known pose.

### PR2 — drivable base + Nav2 planning + holonomic control
PR1 surfaced that the base is **welded**, not floating: `fusestatic` folds the
static trunk into the world body, so there is no base joint in the ROS-sim path.
PR2 must therefore first make the base *movable*, then drive it.

- **Free the base** in the ROS-sim path (`write_mjcf_model` with the existing
  `_wrap_base_freejoint` seam) and add a **floor + wheel contact** so the
  velocity-commanded wheels actually move the base instead of dropping it under
  gravity (the in-process backend records this no-floor limitation as deferred).
- **Live ground-truth odometry**: swap `ground_truth_odom`'s constant-pose seam
  for the dfki-ric `GetBodyState` service (`/mujoco_get_body_state`) so
  `odom → base_link` tracks the now-movable base.
- Bring up costmaps + a global planner + a holonomic controller (DWB or MPPI
  configured for omni-drive, not differential).
- Wire `cmd_vel` (Twist) → 3 wheel velocities through the omni inverse
  kinematics (the mapping LeRobot's `lekiwi.py` driver uses, `base_radius=0.125`,
  `wheel_radius=0.05`) into PR8b's `base_velocity_controller`.
- **Test:** a `NavigateToPose` action goal actually drives the base to the goal
  pose in sim (position + heading converge), not a teleport.

### PR3 — semantic `navigate_to` bridge
- Resolve a named location → reference pose (via `robot_world`) → Nav2 goal, so
  the skill-level `navigate_to('kitchen')` drives the base instead of
  teleporting it.
- Update `robot_world`'s `start_location` on arrival (the world store already
  owns this field).
- **Test:** `navigate_to('kitchen')` drives the base to kitchen's reference pose
  and the store reports the new `start_location`; a query→nav→query round-trip
  is consistent.

## Merge order
```
PR1 ─► PR2 ─► PR3
```
Sequential (same track). One dispatch slot.

## Open risks
- **Base-pose source** (odometry origin in the dfki-ric sim) is the real design
  question — PR1 pins it by probing the sim, not by assumption.
- **Holonomic controller support in Nav2** (DWB omni vs MPPI) — PR2 picks one
  and writes the rationale; differential is explicitly wrong for a 3-omniwheel
  base.
- **MoveIt arm planning** is the *second* half of step 5 and is out of scope
  here — it gets its own breakdown after this track lands.
