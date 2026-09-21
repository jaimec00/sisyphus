# Implementation — issue #125: rim-roller omniwheel model (sim fidelity for holonomy)

**Branch:** `feat/i125-rim-roller-omniwheel` · **Worktree**:
`~/worktrees/i125-rim-roller-omniwheel` on node `olivia` · **Implementer:** worker subagent.

## What changed

### 1. Rim rollers in the sim path (`src/robot_description/robot_description/mjcf_model.py`)

New `_add_rim_rollers(merged)` (called from `write_mjcf_model` **only** when
`base_free_joint=True`) plus helpers `_roller_body_xml` / `_shrink_hub_radius`.
It gives each of the three drivable wheel-link bodies (`base_left_wheel_link`,
`base_back_wheel_link`, `base_right_wheel_link`):

* **N = 8** roller `<body>` children at rim angles `theta_k = 2*pi*k/8`, each at
  `pos = (R_C*cos, R_C*sin, 0)` in the wheel-link frame (local +z = wheel spin
  axis, rim circle in local xy);
* a **free `hinge` joint**, axis = the rim **rolling-tangent**
  `d = (-sin theta, cos theta, 0)` (parallel to the wheel plane, orthogonal to
  the spin axis), no range limit, **no actuator**;
* a **capsule** barrel geom (cross-section `r_roller = 0.007`, half-length
  `0.01` → full length 0.02 < `wheel_width` 0.03) oriented along `d` via
  `fromto`;
* a small positive `<inertial>` (mass 0.01 kg, diaginertia
  `4.56e-7 4.56e-7 2.45e-7`).

Constants: `R_C = wheel_radius - r_roller = 0.043` (roller outer surface back at
the 0.05 contact radius the floor already assumes).

**Hub shrink (MJCF-only):** the wheel hub cylinder geoms are rewritten from
`size="0.05 0.015"` to `size="0.040 0.015"` **and** `contype="0"
conaffinity="0"` (the hub must not collide, since it radially overlaps the
rollers and, being on a different body, would otherwise generate hub↔roller
contacts). The URDF is untouched (D29 gate stays green).

Roller hinge axis rationale (load-bearing design point): a real omniwheel grips
in the rolling direction and lets the contact **slide freely along the wheel's
axle**. So the roller's own spin axis must be the **rolling direction `d`**
(sliding along the axle spins the rollers), *not* the axle. An earlier attempt
set the axis = the axle (rim tangent `t`) — that inverts it (wheel skates
forward, grips sideways) and was rejected by probe.

### 2. Write-path gate (`src/robot_description/test/test_mjcf_drive.py`)

* `ROLLER_NQ = 18 + 7 + 24 = 49`; `ROLLER_NV = 18 + 6 + 24 = 48`;
  `ROLLER_NBODY = 19 + 1 + 1 + 24 = 45` (used by the free-joint tests).
* New tests: roller presence/count/passivity (each wheel has exactly 8 roller
  child bodies, each with one **free** hinge joint — no range, no actuator — with
  an axis in the wheel plane orthogonal to the spin axis); hub shrunk below the
  roller radius; rollers (not the hub) touch the floor at home;
  `nu == 18` on the drivable path (rollers unactuated).
* Welded escape hatch (`base_free_joint=False, floor=False`) keeps
  nq=nv=18 / nbody=19, **no rollers** — asserted explicitly.

### 3. Nav2 `vy` re-enabled (`src/robot_nav/params/nav2.yaml`)

`FollowPath` MPPI block: `vy_std: 0.2`, `vy_max: 0.30`, `vy_min: -0.30`
(mirroring vx); the "vy parked at 0" comment is updated.

### 4. Acceptance (`src/robot_bringup/test/test_pr2_navigate.py`)

* The existing open-loop direction claim is kept.
* New closed-loop claim `test_base_converges_on_a_lateral_navigate_to_pose_goal`:
  sends a real `nav2_msgs/action/NavigateToPose` goal (off-axis pose
  `GOAL_X=0.60, GOAL_Y=-0.45, GOAL_YAW=-1.0`, exercising vy + wz) on
  `/navigate_to_pose` and asserts the ground-truth `GetBodyState('base_link')`
  pose converges in position AND heading within `xy_goal_tolerance 0.10` /
  `yaw_goal_tolerance 0.15`. Reuses the existing DDS / `/dev/shm` hardening
  (distinct `ROS_DOMAIN_ID` per session, per-probe subprocess, liveness-guarded
  scoped sweeps + orphan reaping, bounded bringup retry) unchanged.

## Resulting model counts (drivable write path)

| | value |
|---|---|
| nq | 49 |
| nv | 48 |
| nbody | 45 |
| nu | 18 (unchanged — rollers unactuated) |
| njnt | 43 (18 URDF + 1 free + 24 roller hinges) |

Welded escape hatch: nq=nv=18, nbody=19, no rollers, no floor (unchanged).

## Open-loop verification (R9) — exact numbers

Open-loop via `GetBodyState('base_link')` (and a raw-quaternion cross-check), and
independently in **isolated pure MuJoCo** (same derived model file, same
`body_to_wheel` wheel speeds, real velocity actuators, base settled 1 s first —
SHA-1 of the derived MJCF verified identical to the file the ROS launch loads).

### Before (PR2, plain-cylinder plant — issue #124 baseline)

| command | measured |
|---|---|
| pure `+vy` | **yaw spin, no lateral drift** |
| pure `+wz=0.6` | dyaw ≈ **0.36×** commanded (scrub) |
| `vx+wz` | ≈ **0.16×** commanded and misdirected |

### After (rim-roller plant)

The Nav2 keys set are `vy_std`, `vy_max`, `vy_min` (mirroring vx), per R7.

**Translation is fixed.** Isolated pure-sim, WHEEL_SIGN=-1 / WZ_SIGN=+1:

| command | duration | expected | measured | ratio |
|---|---|---|---|---|
| `+vx = 0.3` | 4 s | dx = +1.20 | dx = **+1.165, dy = -0.187, yaw = -0.183** | **0.97×** |
| `+vy = 0.2` | 4 s | dy = +0.80 | dy = **+0.805, dx = -0.063, yaw = +0.088** | **1.01×** |

ROS sim open-loop (`_drive_probe`), fresh session per direction:

| command | duration | expected | measured | ratio |
|---|---|---|---|---|
| `+vx = 0.3` | 5 s | dx = +1.50 | **dx = +1.499, dyaw = +0.007** | **1.00×** |
| `-vy = 0.2` | 5 s | dy = -1.00 | **dy = -1.019, dyaw = +0.057** | **1.02×** |

Lateral drift now tracks the commanded `∫vy dt` with no sustained yaw — R9's
`+vy` target is met, and forward drive is exact. This is the core #125 fix.

**Rotation (`wz`) is the open problem — wrong sign.** Both the ROS sim (raw
`base_link` quaternion: start yaw ≈ +0.02, end `wz≈-0.913`, yaw = **-2.30 rad**
for `+wz=0.6` over 5 s) and the isolated pure-sim (`+wz=0.6` → yaw ≈
**-0.6 rad/s**, magnitude ≈ 1.0×) show the base rotating **-yaw** for a `+wz`
command under the shipped `WZ_SIGN=+1.0`. The old cylinder plant scrubbed to
0.36× *with the correct sign*; the roller plant removed the scrub (magnitude now
≈1.0×) but the channel comes out **inverted**.

Roller-count sweep (isolated pure-sim, WZ_SIGN=+1):

| N | wz yaw (exp +2.4) | vx (exp 1.2) | vy (exp 0.8) |
|---|---|---|---|
| 8 | -2.401 | 1.165 | 0.805 |
| 12 | -2.435 | 1.180 | 0.823 |
| 16 | -2.522 | 1.113 | 0.837 |
| 24 | -2.610 | 0.381 (jams) | 0.862 |

**N=8 was kept** (R3's starting value): translation is robust through N=16; N=24
jams forward drive (rollers too dense), and no N fixes the wz sign.

Flipping `WZ_SIGN` to -1.0 in the isolated pure-sim makes pure `wz=0.6` give
yaw = **+2.377 (0.99×, correct sign)** while leaving vx/vy intact — i.e. the
roller plant appears to invert the wz channel relative to the cylinder plant the
sign was calibrated on.

## Closed-loop acceptance (R8) — does NOT converge on this branch

`test_base_converges_on_a_lateral_navigate_to_pose_goal` was run against the live
sim. The `NavigateToPose` goal **did not succeed**:

| run | WZ_SIGN | dx | dy | dyaw (goal -1.0) |
|---|---|---|---|---|
| goal probe | +1.0 (as shipped) | **-3.075** | +0.119 | -0.096 |
| goal probe | -1.0 (temporary test) | -0.653 | +0.227 | +0.013 |

With the branch as shipped (WZ_SIGN=+1), the base drives **away** from the goal
and never rotates to it. Temporarily flipping WZ_SIGN to -1.0 changed the
translation direction toward the goal but still did not converge: the base still
does not rotate (dyaw ≈ 0). The wz channel is not delivering controlled heading
in the ROS closed loop — expected, given the inverted wz channel above.

## Deviation / fork flag (NOT silently applied)

**R6 forbids touching `WHEEL_SIGN` / `WZ_SIGN` without VERIFIED evidence.** The
evidence gathered here indicates the roller plant inverts the wz channel
(magnitude ≈ 1.0×, sign inverted under WZ_SIGN=+1, in isolated pure-sim *and*
via the ROS raw quaternion), which explains the failing closed-loop goal. Per the
ruling the sign was **left untouched** (`WZ_SIGN = +1.0`); the temporary -1.0
experiment was reverted. **This is an escalation, not an applied change.**

### Residual issues

* Pure `+vy` carries a small spurious yaw (≈ -0.30 rad / 4 s in isolated sim;
  ≈ +0.06 rad / 5 s in ROS). Might be the roller-passing polygon effect.
* Only one roller per wheel contacts the floor at a time (expected for an
  omniwheel); the base's angular velocity oscillates at the roller-passing
  frequency. Increasing N did not remove it and N=24 jams vx.
* The wz magnitude in ROS sessions varied (a session that showed ~0.72× and
  another ~0.29×) before the raw-quaternion cross-check settled the sign
  question; red-team should re-run and re-measure the magnitude.

## R10 applied — `WZ_SIGN` flipped to -1.0

The red-team verdict: the roller **model is sound**; the only BLOCK was the wz
channel inversion.  Residual phenomena are NOTE-level only (a small +vy yaw and
the roller-passing ripple).  R10 applied in this commit:

* `src/robot_nav/robot_nav/omni_base_controller.py` — `WZ_SIGN` flipped
  `+1.0 -> -1.0`, so the bridge now applies a single unified global `-1.0` to
  every column (matching `WHEEL_SIGN = -1.0`).  The wz column comes out negated:
  the #125 rim-roller plant inverts the rotational channel, whereas the
  `+1.0` was calibrated against the PR2 plain-cylinder plant (which gave
  inverted yaw under a global -1.0).  Constant docstrings, the module
  "Sign convention" paragraph, and the `body_to_wheel` docstring updated to
  state the unified -1.0 (no more "split by column").
* `src/robot_nav/test/test_omni_ik.py` — the sign tests now pin the unified
  -1.0: `test_calibrated_sign_is_unified_negative_one` (both constants `-1.0`;
  the default output IS the global negation of the LeRobot raw; the wz part is
  negated), `test_default_pure_wz_rotates_negative` (pure `+wz` yields negative
  wheel speeds), and the translation test's stale docstring fixed.  All 10
  pure-function tests pass.
* `src/robot_bringup/test/test_pr2_navigate.py` — stale pre-#125 docstrings
  fixed (they still described the plain-cylinder scrubbing plant and said
  closed-loop was "deferred to post-#125"): module docstring, the
  `MIN_VX_DX`/`MAX_VX_YAWR` threshold comment block, `_drive_probe_worker`, and
  the open-loop test's docstring.  Threshold VALUES are unchanged (direction-only
  claims kept); the closed-loop test docstring's historical contrast is
  untouched.  No test logic or thresholds changed.

## Re-scope (Jaime decision B, 2026-09-20)

The **pure-channel goal is MET**: `+vx 0.98x`, `+vy 1.02x`, `+wz +1.04x` (positive,
correct sign) — verified in isolated pure sim *and* through the ROS path. That
was the core of #125 (make the lateral + rotational channels actually translate/
rotate at commanded speed by modelling the rim rollers).

The **closed-loop `NavigateToPose` convergence acceptance is deferred to #127**.
The blocker is that a combined `vx+wz` wheel command does not compose in sim
(measured dx 0.17x commanded + spurious dy ~0.85) — a **pre-existing PLANT
defect** (the rollers slip/whirl rather than grip), not a bridge/sign bug. It is
the new prerequisite for closed-loop navigation convergence.

`test_base_converges_on_a_lateral_navigate_to_pose_goal` is therefore now a
`pytest.skip` pending #127 (its `_goal_probe`/`_goal_probe_worker` helpers and
`GOAL_*`/`*_TOLERANCE` constants are left in place, ready for re-enablement).

## Root cause of the "wz inverted in the ROS path" symptom — MEASUREMENT aliasing (H1), not a plant inversion

The test-runner came back RED on `test_base_drives_under_wheel_commands`: pure
`+wz=0.6` reported `dyaw ≈ -1.42 rad`, i.e. the ROS path looked like it rotated
`-yaw` while the isolated sim rotated `+yaw`. Two hypotheses were discriminated
by reading the **raw** `GetBodyState('base_link')` quaternion (not the derived
yaw) in both paths.

**VERIFIED — the plant rotates `+yaw` in BOTH paths; only the measurement
wrapped.** Raw quaternion evidence, pure `+wz=0.6`, all three wheel joints at
`-1.5 rad/s` in both paths (identical commands):

| path | window | start `(x,y,z,w)` | end `(x,y,z,w)` | `wrap(end-start)` | TRUE (unwrapped) dyaw |
|---|---|---|---|---|---|
| isolated sim (direct hinge joints) | 4 s | `(-0.000106, 0.000378, -0.005346, 0.999986)` | `(-0.001024, 0.000569, 0.947816, 0.318815)` | **+2.503 rad** | +2.503 rad (+143°) |
| isolated sim | 8 s | (same start) | `(z=+0.640, w=-0.768)` | **-1.372 rad** | **+4.911 rad (+281°)** |
| ROS path (`mujoco.launch.py` + `/cmd_vel`) | 8 s | `(-0.000010, 0.000599, 0.009399, 0.999956)` | `(-0.001794, -0.000847, 0.640447, -0.768000)` | **-1.409 rad** | **+4.874 rad (+279°)** |

The ROS controller chain is confirmed clean end to end: the bridge publishes
`[-1.5, -1.5, -1.5]`, and `/joint_states` shows the three wheel joints tracking
at `-1.500 .. -1.518 rad/s` — the same wheel speeds the isolated probe applies
directly. A continuous yaw trace over the 8 s window rises monotonically
(`+1° → +39° → +75° → +109° → +143° → +178° → +213°(wrapped) → +247°`), i.e.
steadily **positive**.

**The defect is in the probe's measurement.** `_drive_probe_worker` computed
`dyaw = _wrap(end_yaw - start_yaw)` — a *single* wrap into `(-pi, pi]`. With
`WZ_DRIVE_S = 8.0 s` at ~0.61 rad/s the base turns `≈ +4.9 rad (≈ +280°)`, which
a single wrap aliases to **`-1.41 rad`** — exactly the reported symptom. The
sign looked inverted; the plant was rotating the right way the whole time.

**Fix:** `_drive_probe_worker` now accumulates the yaw **incrementally** (each
per-sample delta over the 20 ms loop is far below `pi`, so it is unambiguous)
and returns the summed `dyaw`, instead of wrapping the total once. Post-fix the
open-loop probe returns `dyaw = +4.871 rad (+279°)` for `+wz=0.6` (threshold
`>= 0.5`), and `+vx` is unchanged (`dx +1.0`-class, `|dyaw|` small).

**`WZ_SIGN` stays `-1.0` — and is now independently justified.** With the
measurement fixed and no aliasing, both paths agree: `WZ_SIGN = -1.0` gives
`+wz → +yaw`; `WZ_SIGN = +1.0` gives `-yaw` (isolated sim, 4 s) -- so the `-1.0` is
the *clean* calibration, consistent with the translational column's joint-axis
convention. The earlier `+1.0 -> -1.0` flip (R10) reached the right constant but
partly on the strength of the aliased ROS reading; this section replaces that
inference with a correct one. Files touched in this fix:

* `src/robot_bringup/test/test_pr2_navigate.py` — `_drive_probe_worker` measures
  yaw incrementally; the `#125 "wz inverted"` symptom explained in a comment.
* `src/robot_nav/robot_nav/omni_base_controller.py` — module + constant
  docstrings: the wz `-1.0` is a clean joint-axis fact, with the wrap pitfall
  called out for anyone re-calibrating.
* `src/robot_nav/test/test_omni_ik.py` — `test_pure_wz_turns_every_wheel_equally`
  docstring reformatted (D205/D209/D400); the sign test docstrings de-claim the
  "plant inverts the wz channel" framing.

**Pitfall for the future (recorded):** never calibrate the yaw sign by wrapping
`end - start` over a window that turns more than `pi`. Measure incrementally or
keep the window under half a turn.
