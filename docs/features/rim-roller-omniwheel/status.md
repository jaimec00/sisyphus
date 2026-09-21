# Feature status — issue #125: rim-roller omniwheel model (sim fidelity for holonomy)

**Worktree:** `~/worktrees/i125-rim-roller-omniwheel` on node `olivia`
(branch `feat/i125-rim-roller-omniwheel`, cut from `origin/main` @ `69d34da`).

**Manager:** Sisyphus (worktree manager, `deepseek-v4-pro`).

## The brief (one line)
The sim models the 3 omniwheels as plain cylinders (URDF collision is a
`cylinder`), so a cylinder rolls tangentially but will not slide along its axle
(lateral) nor rotate about vertical (yaw) without scrubbing. Measured (PR2):
pure `vy` → yaw spin, no lateral drift; pure `wz=0.6` → ~0.36× commanded (scrub);
`vx+wz` → ~0.16× commanded and misdirected. Fix = model the rim rollers in the
**sim path** so lateral + rotational channels actually translate/rotate at
commanded speed, then re-enable `vy` in Nav2 and restore the closed-loop
`NavigateToPose` convergence acceptance.

## Probing findings (what I read, grounded in the source)

- **The MJCF is derived, never checked in.** `robot_description.mjcf_model`
  expands `robot.urdf.xacro`, imports into MuJoCo (`MjModel.from_xml_path` +
  `mj_saveLastXML`), splices the hand-authored `mjcf/overlay.xml`, and — for the
  drivable model — wraps the trunk in a free-jointed `base_link` and injects
  `<option integrator="implicit"/>`. Three public entry points:
  - `load_mjcf_model()` → bare **welded** model (fusestatic), nq=nv=nu=18,
    nbody=19. Pinned by `test_mjcf_model.py`. This is the in-process backend's
    *bare* loader (used only by tests; the backend itself uses the next one).
  - `load_mjcf_model_with_scene(world_bodies)` → free-jointed + scene bodies
    (nq=25, nv=24). This is the in-process `MuJoCoBackend` (D34) — `navigate_to`
    is a **teleport** (no `mj_step`, no wheel dynamics, no floor; scene geoms are
    contype/conaffinity=0). It never drives wheels.
  - `write_mjcf_model(path, *, base_free_joint=True, floor=True)` → the
    **drivable ROS-sim model** the `mujoco_ros2_control` launch actually loads
    (`mujoco.launch.py:109` calls `write_mjcf_model(mjcf_path)`). nq=18+7=25,
    nv=18+6=24; floor + free joint + implicit integrator. Pinned by
    `test_mjcf_drive.py`.
- **The wheels** (`urdf/base.xacro`, `omni_wheel` macro): each wheel is a
  `continuous` joint `base_{left,back,right}_wheel` → `base_{left,back,right}_wheel_link`,
  child +z = spin axis pointing radially outward (rpy `(0, π/2, φ)`), a `cylinder`
  geom radius `wheel_radius=0.05` length `wheel_width=0.03`, solid-cylinder
  inertia. Mounts left/back/right = 60/180/300°. This is the D29 base-geometry
  gate (`test_description.py`) — it parses the **URDF**, not the MJCF.
- **The wheel velocity actuator** (`overlay.xml`): `<velocity name="base_*_wheel"
  joint="base_*_wheel" kv="10"/>`. dfki-ric classifies `<velocity>` as VELOCITY
  and writes wheel speed straight to ctrl. `nu=18` (3 velocity + 15 position).
- **The omni IK** (`robot_nav/omni_base_controller.py`): `body_to_wheel` with
  `WHEEL_SIGN=-1.0` (translational) / `WZ_SIGN=+1.0` (rotational), `base_radius=0.125`,
  `wheel_radius=0.05`. **Do not touch the signs** (the brief + PR2 calibration
  say the defect is the plant/contact, not the bridge) — unless the implementer
  produces VERIFIED evidence they are wrong.
- **Nav2 vy parked** (`robot_nav/params/nav2.yaml`, `FollowPath` MPPI): `vy_std=0.0`,
  `vy_max=0.0`, `vy_min=0.0`. Must be restored (`vy_std~0.2`, `vy_max=+0.30`,
  `vy_min=-0.30`).
- **Acceptance** (`robot_bringup/test/test_pr2_navigate.py`): currently the
  re-scoped open-loop direction claim `test_base_drives_under_wheel_commands`,
  with the full DDS/`/dev/shm` hardening (distinct `ROS_DOMAIN_ID` per session,
  per-probe subprocess, liveness-guarded scoped sweeps + orphan reaping, bounded
  bringup retry). No `NavigateToPose` goal is sent today.

## Manager rulings

### R1 — The roller model is a **sim-path-only** change, not shared geometry.
Add rim rollers **only** to `write_mjcf_model`'s drivable model (gated on
`base_free_joint=True`), leaving `load_mjcf_model` (welded, nq=nv=18) and
`load_mjcf_model_with_scene` (in-process backend) byte-for-byte untouched.

- **Why not shared:** the in-process `MuJoCoBackend` never drives wheels —
  `navigate_to` is a teleport and its scene has no floor/contact (R6). Rollers
  are a *contact-fidelity* concern that only matters where wheels touch ground
  under velocity command (the ROS-sim path). Adding them to the shared
  `_build_merged_mjcf` would ripple through `test_mjcf_model.py` (nq/nv/nbody),
  the backend's name-based lookups and its `_joint_ids_excluding`/`_home_joints`
  sweeps for zero benefit.
- **Why not the URDF:** the URDF is the single source of truth for the *robot's*
  geometry (D29). The rollers are a sim-contact overlay, exactly the category
  `overlay.xml` already carries (actuators, contact defaults, sensors). Keeping
  the URDF wheel a plain cylinder leaves the D29 gate (`test_description.py`) and
  `robot_state_publisher`/TF/KDL untouched.

This satisfies the brief's invariant: "in-process MuJoCoBackend path must stay
green and untouched" (we keep it untouched, so no shared-geometry justification
is needed).

### R2 — Injection point: a new `_add_rim_rollers(merged)` in `mjcf_model.py`.
Generate the roller `<body>` blocks programmatically (same f-string style as
`_wrap_base_freejoint` / `FLOOR_BODIES`), NOT in `overlay.xml` — the overlay
splice has no per-body seam and the roller count/shape must stay parametric.
Call it from `write_mjcf_model` **only** when `base_free_joint=True`, after
`_wrap_base_freejoint` (order vs floor insertion is irrelevant — floor is a
world-body sibling; rollers are wheel-link children). The bare welded escape
hatch (`base_free_joint=False, floor=False`) must still reproduce the PR8b
welded model (nq=nv=18, nbody=19, no rollers).

Each roller is a child `<body>` of `base_{left,back,right}_wheel_link` (those
body names are confirmed present in the write path — `test_mjcf_drive.py`
resolves them). Per wheel, `N` rollers at rim angles `θ_k = 2πk/N`, in the wheel
link frame:

- position `(r_center·cos θ_k, r_center·sin θ_k, 0)`;
- a free `hinge` joint, axis `(-sin θ_k, cos θ_k, 0)` (tangent to the rim), no
  actuator, no range limit (free spin);
- a barrel/crown geom (capsule or cylinder) whose long axis is the tangent axis,
  small cross-section radius `r_roller`, length `≈ wheel_width` (≤ 0.03);
- a small positive mass/inertia (> `mjMINVAL`).

### R3 — Roller geometry + hub shrink.
- **Count:** `N = 8` rollers per wheel (24 total) — the starting ruling. Enough
  for near-continuous contact (a contact every 45° with barrel geometry) while
  keeping the DOF addition modest. The implementer may probe 6/10 and, if
  contact is bumpy or insufficient, adjust and record the evidence + the final
  value in `implementation.md` (this ruling is not sacred; the acceptance numbers
  are).
- **Dimensions:** `r_roller ≈ 0.007 m` (roller cross-section radius),
  `r_center = wheel_radius - r_roller = 0.043 m` (roller-centre radius), so the
  roller outer surface reaches the 0.05 contact radius the floor height and
  `base_footprint` already assume. Roller length ≈ 0.02 m (within `wheel_width`).
- **Hub shrink (MJCF-only):** the wheel hub's *collision* geom radius must drop
  below `r_center` (e.g. 0.040) so the hub cylinder never touches the floor —
  only the rollers contact. Do this in the derived MJCF text, never the URDF.
  The visual geom may follow or stay (physics only cares about the collision
  geom).

### R4 — Rollers are passive; `nu` stays 18.
The rollers add **unactuated** hinge joints. The wheel hub keeps its
`<velocity>` actuator (drives forward rolling). `nu` is unchanged (3 wheel
velocity + 15 position). The rollers inherit the overlay `<default>` contact
defaults; the implementer may add per-roller friction/solref only if the probe
shows it is needed (record what/why).

### R5 — nq/nv/nbody ripple (write path only) and the test constants.
Each roller adds 1 body + 1 hinge → `+1` nq, `+1` nv, `+1` nbody.

- Drivable write path: **nq = 18+7+24 = 49, nv = 18+6+24 = 48, nbody = 19+1(base_link)+1(floor)+24 = 45** (with N=8).
- `test_mjcf_drive.py` must update `FREEJOINT_NQ`/`FREEJOINT_NV` (and add/update
  any nbody assertion) to the roller-inclusive counts; add a dedicated test that
  asserts the roller bodies/joints exist (names, count, hinge type, tangent axes)
  and that `nu` is still 18. The welded escape-hatch test stays 18/18/19
  unchanged — it must still pass without rollers.
- `test_mjcf_model.py` (bare loader) and `test_mujoco_backend.py` must **not**
  change — the bare loader and the backend are untouched by R1.

### R6 — Do not touch the omni IK signs.
`omni_base_controller.py`'s `WHEEL_SIGN=-1.0` / `WZ_SIGN=+1.0` are correct; the
defect is the plant (wheel contact), not the bridge. Only change them with
VERIFIED evidence to the contrary (and that would be an escalation, not a
silent edit).

### R7 — Re-enable `vy` in Nav2.
In `robot_nav/params/nav2.yaml` under `FollowPath` (MPPI), restore
`vy_std: 0.2`, `vy_max: 0.30`, `vy_min: -0.30` (mirroring vx), and update the
comment that currently documents "vy parked at 0".

### R8 — Restore the closed-loop acceptance.

**[DEFERRED to #127]** — the closed-loop test is skipped on this issue (#125 accepts the pure-channel win; coupled `vx+wz` does not compose).
`test_pr2_navigate.py` must gain (or re-scope to) a closed-loop claim: send a
real `NavigateToPose` action goal whose pose exercises **lateral (vy) + rotational
(wz)** channels, and assert the base **converges in position AND heading** within
the goal tolerances (`xy_goal_tolerance 0.10`, `yaw_goal_tolerance 0.15`). Keep
the PR2 DDS/`/dev/shm` hardening pattern (distinct `ROS_DOMAIN_ID` per session,
per-probe subprocess, liveness-guarded scoped sweeps + orphan reaping, bounded
bringup retry) — reuse it, don't rewrite it. Keep the existing open-loop
direction claim (it still proves the stack composes and the signs hold); the
closed-loop test is the new #125 acceptance.

### R9 — Verification by command (record exact numbers in `implementation.md`).
Open-loop in sim via `GetBodyState('base_link')`, same `_drive_probe` mechanism:
- pure `+vy` (e.g. 0.2 m/s for ~5 s) → translates laterally (dy ≈ ∫vy dt),
  **no** sustained yaw spin;
- pure `+wz` (0.6 rad/s) → rotates at ≈ commanded rate (dyaw ≈ 0.6·t; the scrub
  was 0.36×, target ≈ 0.7–1.0×);
- `vx=0.3 + wz=0.6` → clean translation ≈ commanded direction/speed, no
  scrub/jam.
Record the before/after numbers and the exact thresholds chosen in
`implementation.md`.

### R10 — Flip `WZ_SIGN` to `-1.0` (plant calibration, authorized).
R6 forbade touching the signs without VERIFIED evidence; that evidence now
exists (isolated pure-sim `+wz` -> `+yaw` at 0.99x with `WZ_SIGN=-1.0`, and the
ROS raw-quaternion inversion under the shipped `+1.0` -- both in
`implementation.md`). The roller plant's contact kinematics invert the rotation
channel relative to the cylinder plant PR2 calibrated `WZ_SIGN=+1.0` on; that
`+1.0` was only correct for the broken cylinder plant. Flip to `WZ_SIGN=-1.0`,
which now matches `WHEEL_SIGN=-1.0` (a cleaner, unified global sign). Apply in
`omni_base_controller.py` (constant + its docstring + the module docstring's
sign-convention paragraph), `test_omni_ik.py` (the sign-constant unit tests +
their docstrings), and record in `implementation.md`.

**[APPLIED]** WZ_SIGN flipped +1.0 -> -1.0 (unified global -1.0) with the
sign-constant unit tests rewritten to pin it, plus the stale pre-#125
docstrings in `test_pr2_navigate.py` fixed — landed in this commit.

### R11 — Closed-loop convergence is the #125 acceptance; investigate, don't assume.

**[DEFERRED to #127]** — closed-loop convergence is no longer the #125 acceptance; #127 owns it.
After R10: (a) CLEAN rebuild + reinstall of `robot_nav` (`pixi run build`), then
(b) re-run `test_base_converges_on_a_lateral_navigate_to_pose_goal` in
`test_pr2_navigate.py`. Verify the node actually logs `WZ_SIGN=-1.0` before
drawing any conclusion. If it converges -> proceed. If it STILL fails, diagnose
by running (not reasoning): (1) is `/odom` twist reporting wz with the correct
sign (cross-check vs `GetBodyState` raw quaternion)? (2) is MPPI commanding a
non-zero wz toward the goal heading? (3) does an open-loop `+wz` now rotate the
base `+yaw` in the ROS path (not just isolated sim)?


**[RESULT: FAIL]** Clean rebuild green (14 pkgs, 10.7 s), `WZ_SIGN=-1.0` confirmed
live in the node log, but the closed-loop test fails deterministically (3x
reproduced): goal (0.60, -0.45, yaw -1.00) accepted, then ABORTED — the base
diverged to x≈-3.25 (xy error ~3.9 m vs 0.10 m tol), planner aborts out-of-bounds.
Open-loop in the ROS path: +vx ✓ (dx +1.01), +wz ✓ (dyaw +1.9..2.1), +vy ✗
(spurious yaw +1.35 and -x drift, run-to-run inconsistent).

## Open questions / risk
- **Roller contact smoothness** (does N=8 give clean rolling, or is it bumpy /
  does the wheel catch roller edges?) — the implementer probes; this is the
  highest-risk part and why the red-team must run the sim, not reason about it.
- **dfki-ric compatibility** with 24 extra passive hinge joints — unactuated
  joints should be inert to its PRM actuator classifier (it maps actuators by
  name), but the implementer must confirm the sim still spawns and the wheel
  velocity commands still track.

## Escalation — coupled-channel fork remains after R10 (BLOCK)

R10 is correct and regression-free for the PURE channels (re-red-team, pure-sim
@ WZ_SIGN=-1.0): +vx 0.98x, +vy 1.02x, +wz 1.04x positive; 10/10 unit tests;
diff is constant+docstrings+tests only, split formula preserved. But the
COMBINED command does NOT compose: vx=0.3+wz=0.6 -> dx +0.20 (0.17x commanded),
dy +0.85 (should ~+0.10). The sign flip only MIRRORED dy (old +1.0: dx+0.21/
dy-0.83 -> new -1.0: dx+0.20/dy+0.85); corruption magnitude unchanged.

This is a PLANT defect, not a bridge/sign bug: (a) wheel speeds decompose exactly
linearly in `body_to_wheel`, so the controller is correct; (b) the plain-cylinder
plant ALSO fails vx+wz (dx +0.18) — pre-existing, independent of #125; (c) the
rollers spin at extreme |qvel| ~260-325 rad/s during ALL commands (incl. pure
+vx, where a gripping roller should be ~0) — they SLIP/WHIRL, not grip. #125
fixed the pure channels but left the coupled channel: R9's "vx+wz -> clean
translation" criterion is NOT met, and R8/R11 closed-loop convergence FAILS.

-> ESCALATED to Jaime: genuine modeling/control fork after R10.

**RESOLVED (Jaime, 2026-09-20 — decision B):** re-scoped #125 to the pure-channel
win (vx/vy/wz all clean and correct-signed), filed **#127** (coupled-channel
`vx+wz` non-composition — a pre-existing plant defect) as the new prerequisite for
closed-loop nav, and `test_base_converges_on_a_lateral_navigate_to_pose_goal` is
now skipped pending #127.

## Build-environment note (pre-existing, out of scope but blocking a fresh worktree)

The vendored `dfki-ric/mujoco_ros2_control @ f151b7d` fails to compile
`simulate_gui` on a **fresh** `pixi run build`: the conda env's mujoco 3.12.0
headers (`$CONDA_PREFIX/include/mujoco/mujoco.h`) shadow the FetchContent'd
MuJoCo 3.9.0 headers, and the `mjv_moveCamera` signature drift breaks the build
(`cannot convert mjvScene* to mjvCamera*`). PR2 already hit + worked around this
with a local (git-untracked) patch to the vendored
`mujoco_ros2_control/CMakeLists.txt` that **prepends**
`${CMAKE_BINARY_DIR}/_deps/mujoco-src/include` + `simulate` to the
`include_directories(...)` block (so the 3.9 headers win). Re-applied here. It
is **not** persisted anywhere tracked in the robot repo — every fresh worktree
re-hits it until someone lands the patch permanently (a follow-up, not a #125
blocker).

## Dispatch plan
implementer → red-team (run, don't reason) → fix → re-red-team (N+1) →
test-runner (full `pixi run test`; ratchet `scripts/test_baseline.json` if
counts grow) → squash-merge PR. Manager opens the PR; does not merge.

**DESIGN FORK — escalation required (post-R10).** The coupled-channel
(vx+wz / off-axis) command does not compose; root cause is the roller contact
(slip, not grip), pre-existing and not fixed by #125. See "## Escalation" above.
