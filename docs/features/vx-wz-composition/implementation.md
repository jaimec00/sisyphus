# Implementation — issue #127: combined `vx+wz` composition

**Branch:** `feat/i127-vx-wz-composition` · **Worktree:**
`~/worktrees/i127-vx-wz-composition` on node `olivia` · **Implementer:** worker
subagent.

## Headline

**The assigned premise is false: the roller plant already composes.** A combined
`vx+wz` command delivers *both* channels at ~1.0x simultaneously in the body
frame, with no off-axis slide and with the rollers gripping. The "combined does
not compose" reading was a **probe-measurement artifact** (a cold, ~1 s-ramping
run compared against an ideal instant-velocity arc, measured in the world
frame).

The closed-loop `NavigateToPose` acceptance *does* still fail — but the cause is
in the **Nav2 controller layer**, not the contact model. A plant-only change
(R1/R2's scope) cannot fix it. **No change to `mjcf_model.py` was made.**

## 1. The plant composes (measured, not reasoned)

Identical derivation to `write_mjcf_model` (the file the ROS sim loads), isolated
pure MuJoCo, real velocity actuators, base settled 1 s, measurement window taken
**after** the velocity ramp, twist read in the **body frame**.

### Steady body-frame twist (the quantity the command names)

| command | body linear (m/s) | body yaw (rad/s) | delivered |
|---|---|---|---|
| `vx=0.3` | (+0.2985, +0.0009) | +0.028 | 1.00x / 0.0x |
| `vy=0.2` | (−0.0009, +0.2031) | −0.011 | 1.02x / 0.0x |
| `wz=0.6` | (−0.0004, −0.0005) | +0.5977 | 0.00x / 1.00x |
| **`vx=0.3, wz=0.6`** | **(+0.3007, −0.0040)** | **+0.6093** | **1.00x / 1.02x** |

The combined command delivers forward speed **1.00x** and yaw rate **1.02x at
the same time**, with lateral leak of 0.004 m/s (1.3% of vx).

### Roller "whirl" is rolling, not slip

The task's grip target ("pure `+vx` leaves roller |qvel| ~0") conflates rolling
with slipping: **a rolling roller must spin**. |qvel| ~60 rad/s is the roller
rolling at the commanded ground speed, not scrubbing.

The real grip signal is the **contact-patch slip** — the velocity of the roller
material *at the contact point* (`v_body + ω × (contact_pos − body_xpos)`,
relative to the static floor, projected into the contact tangent plane):

| command | max patch slip (m/s) | vs commanded |
|---|---|---|
| `+vx=0.3` | 0.064 | 21% |
| `vy=0.2` | 0.034 | 17% |
| `wz=0.6` | 0.029 | — |
| `vx+wz` | 0.077 | 26% |

The rollers **grip**: they carry the load, and the residual slip is the
roller-passing hand-off, not a scrub.

> Measurement pitfall (recorded for the future): reading the *body-origin*
> velocity of a spinning roller reports its spin as "slip" (~0.27 m/s) and is
> wrong by more than an order of magnitude. Use the patch velocity.

### Why the original probe said "does not compose"

`probe_i127.py` compares a **cold** 4 s run — the base starts at rest and only
reaches commanded speed after ~1.0 s (measured `t90 = 1.02 s`) — against an
**ideal instant-velocity arc**, and reads `dx` along the **world x-axis** for a
curved path. For `vx=0.3, wz=0.6` over 4 s the ideal arc is
`R = 0.5, θ = 2.4 rad → dx = +0.3377, dy = +0.8687`. The measured
`dx = +0.2002, dy = +0.8548` is exactly the ramp-and-projection loss; `dy`
(along the arc) already matches. Measured over a settled 1 s window instead:

| | measured | ideal | ratio |
|---|---|---|---|
| distance travelled | 0.2964 m | 0.300 m | **0.99x** |
| yaw | 0.6093 rad | 0.600 rad | **1.02x** |

## 2. The real blocker is in the Nav2 layer

Live `NavigateToPose` on the shipped bringup, the test's own goal
`(0.60, −0.45, −1.0)`:

```
RESULT dx=-0.140 dy=+0.051 dyaw=+3.085 succeeded=False
```

Multi-goal run (one bringup, 45 s per goal):

| goal | status | moved |
|---|---|---|
| **straight `+x`, no yaw** | 6 | **dx = −3.156** |
| `+x`, yaw −1.0 | 6 | dx = −0.007 |
| lateral `−y`, no yaw | 6 | dx = −0.004 |
| #127 original | 6 | dx = −0.006 |

**Even a trivially straight-ahead goal fails**: the base is driven to
x ≈ −3.2 m for a goal at x = +0.5. `/cmd_vel` trace during the goal:

```
t=0.5  pos=(-0.148,+0.030)  cmd_vel=(vx -0.300 wz +0.188)
t=2.0  pos=(-0.685,+0.147)  cmd_vel=(vx -0.298 wz +0.039)
t=6.3  pos=(-1.979,+0.246)  cmd_vel=(vx -0.300 wz -0.600)
t=10.2 pos=(-3.173,+0.288)  cmd_vel=(vx -0.300 wz +0.414)
```

MPPI pins `vx` at its reverse limit and oscillates `wz` through ±0.6 rad/s: a
stable limit cycle driving the base away from the goal. The plant tracks every
one of those commands exactly.

### Ruled out by measurement (each verified, not reasoned)

| suspect | measurement | verdict |
|---|---|---|
| roller contact / plant | body-frame twist 1.00x/1.02x combined | **correct** |
| `/odom` twist | odom +0.284 vs ground-truth +0.286 | **correct** |
| `odom → base_link` TF | tracks ground truth (≈0.4 s lag) | **correct** |
| open-loop `/cmd_vel` +0.3 | base drives +0.85 | **correct** |
| wheel joints | track commanded speeds (3.69 vs 3.70) | **correct** |
| map extent | 124×82 @ 0.05, origin (−3.00,−1.10); goal inside | **OK** |
| bringup lifecycle | `bt_navigator`/`planner_server`/`controller_server` ACTIVE; MPPI activated | **OK** |
| `collision_monitor` FootprintApproach | disabling it changes nothing | **not the cause** |
| `PreferForwardCritic` enabling | still drives to −1.7 | **not the cause** |

A goal-mirroring test (`(+0.60,−0.45)` vs `(−0.60,+0.45)`) flips the *first*
commanded `vx` sign with the goal's x sign while the base lands in nearly the
same place for both — i.e. the controller's forward/backward sense relative to
the goal is frame-inconsistent. That is a Nav2/MPPI goal-transform or
configuration problem.

## 3. What this branch changes

Plant-only scope could not fix the closed loop, so the change is to the
**measurement and the regression coverage**, plus keeping the (correct)
closed-loop test honest.

### `src/robot_description/test/test_mjcf_drive.py` (+4 dynamics tests)

New, deterministic, pure-sim tests that pin the *coupled* plant behaviour:

* `test_write_mjcf_model_combined_vx_wz_composes_in_the_body_frame` — the #127
  claim: combined `vx=0.3, wz=0.6` delivers ≥0.7x forward speed **and** ≥0.7x
  yaw simultaneously, with lateral leak ≤0.35x vx.
* `test_write_mjcf_model_pure_channels_deliver_command` — each pure channel at
  ≥0.8x with the other two near zero.
* `test_write_mjcf_model_roller_hinges_roll_rather_than_stall` — the roller
  hinges actually spin under a drive command (the rim-roller model is engaged).
* `test_write_mjcf_model_contact_slip_stays_below_the_command` — the true
  contact-patch slip for pure `+vx` stays ≤0.15 m/s (half the commanded speed).

Helpers `_body_to_wheel`, `_drive`, `_roller_contact_slip`, `_settled_slip`.
Measurement discipline encoded in the helpers: settle 1.0 s, ramp 1.5 s, then
measure 1.0 s **in the body frame**; slip is the **patch** velocity.

Measured margins (assertion vs value):

| assertion | threshold | measured | margin |
|---|---|---|---|
| combined forward speed | ≥0.210 | 0.3007 | 43% |
| combined yaw rate | ≥0.420 | 0.6093 | 45% |
| combined lateral leak | ≤0.105 | 0.0040 | 96% |
| pure `+vx` forward | ≥0.240 | 0.2985 | 24% |
| pure `+vy` lateral | ≥0.160 | 0.2031 | 27% |
| pure `+wz` yaw | ≥0.480 | 0.5977 | 25% |
| `+vx` patch slip | ≤0.150 | 0.0641 | 57% |

Stable across 5 consecutive runs (4 passed each time; 19/19 in the file).

### `src/robot_bringup/test/test_pr2_navigate.py`

* **`_goal_probe_worker` yaw measurement fixed** (R6b): the single
  `_wrap(end_yaw - start_yaw)` is replaced by **per-sample yaw accumulation**
  (a local `_accumulate(pose)` helper) over the **whole goal run** — the
  goal-result wait loop *and* the settle loop — matching what
  `_drive_probe_worker` already does. So a >π turn no longer aliases (the #125
  pitfall: +279° → −81°). The accumulation has to span the goal execution, not
  just the settle loop: the rotation that matters happens while the base drives
  to the goal. `WZ_SIGN` is untouched.
* **`test_base_converges_on_a_lateral_navigate_to_pose_goal` stays skipped**,
  but its docstring is rewritten to the *real* finding: the blocker is the Nav2
  controller (which drives the base away from the goal even for a
  straight-ahead goal), not the roller plant, which is measured to compose at
  ~1.0x. The assertion body (xy/yaw tolerance checks against the goal) is in
  place for whoever fixes the controller.
* Re-enabling it was **not** possible: the test genuinely does not pass, and
  re-enabling a failing test would make `pixi run test` red, which is the merge
  gate. Claiming it "passes with the measurement fixed" would be false — its
  failure is a real convergence failure.

### Roller tuning explored (R3) — all rejected

Recorded in `.dev/notes_i127.md`. Summary:

| knob | result |
|---|---|
| hinge `frictionloss` 1e-4…1e-1 | chaotic; ≥2e-3 **breaks** pure vx |
| hinge `damping` 1e-3…1e-1 | non-monotonic; kills wanted lateral slip (vy 0.82→0.48) |
| hinge `armature` | no clean win |
| **anisotropic friction (condim=6, 5-element)** | **not implementable**: MuJoCo 3.12.0 rejects any geom `friction` with >3 values, and `geom_friction` is `(ngeom, 3)` — no per-geom spin/rolling friction exists |
| roller count N (8…22) | noisy/non-monotonic; N=16 modestly better, N=10 breaks vx |
| roller radius / half-length | chaotic; several break pure vx |

None is justified: the shipped configuration already composes.

## 4. Final parameter values

**Unchanged from #125/#128** (no plant edit on this branch):

| constant | value |
|---|---|
| `_ROLLER_COUNT` | 8 |
| `_ROLLER_RADIUS` | 0.007 m |
| `_ROLLER_HALF_LENGTH` | 0.01 m |
| `_ROLLER_CENTER_RADIUS` | 0.043 m |
| `_HUB_RADIUS` | 0.040 m |
| roller hinge | free (no `frictionloss`/`damping`/`armature`) |
| `WHEEL_SIGN` / `WZ_SIGN` | −1.0 / −1.0 |
| `WHEEL_RADIUS` / `BASE_RADIUS` | 0.05 / 0.125 |
| integrator | `implicit` |

## 5. Closed-loop status

**NOT converging**, and the blocker is outside this issue's plant scope.
`test_base_converges_on_a_lateral_navigate_to_pose_goal` remains `pytest.skip`,
now with an accurate reason.

### Recommended follow-up (new issue)

Root-cause the Nav2 controller driving the base away from the goal:

1. MPPI goal/critic configuration in `src/robot_nav/params/nav2.yaml`
   (`motion_model: Omni`, `PreferForwardCritic`, `PathAngleCritic`,
   `min_y_velocity_threshold: 0.5`).
2. The goal transform into `base_link` (the mirror test shows a sign
   inconsistency that the identity `map→odom→base_link` chain does not explain).
3. Reproduce with `ros2 topic echo /cmd_vel` plus a `NavigateToPose` goal and
   compare the commanded twist sign against the goal bearing.

## 6. Test counts

`robot_description`: 66 → **70** (the 4 new dynamics tests). `robot_bringup`
stays **11** (the closed-loop test is still skipped). `scripts/test_baseline.json`
is ratcheted by the `pixi run test` driver's normal upward pass.

Pre-existing failures in this worktree: `test_flake8` / `test_pep257` /
`test_copyright` for `robot_description` fail on the **unmodified** branch too
(they scan the local `build/` and `install/` trees). Verified by stashing and
re-running.
