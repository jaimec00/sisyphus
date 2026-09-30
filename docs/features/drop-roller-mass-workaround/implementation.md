# implementation.md — i141: drop the #138 roller-mass workaround

## Summary

`_ROLLER_MASS` is restored to its physical value **0.01 kg** (from the #138
0.16 kg workaround) and `_BASE_DIAGINERTIA` stays at its stand-in value
`0.1 0.1 0.1`, **unchanged** (R1).  The 0.01 kg plant originally "stalled"
(pure `+vx` through the ROS chain delivered ~0.5-0.67x), which is what kept the
0.16 kg workaround in place.  The stall is now fixed at the control seam, so
the workaround can be dropped.

## Root cause (confirmed, not re-derived here)

The 0.01 kg "slip" was **not** a roller-contact/solver defect.  It is a **base
pitch tip-over**: a pure `+vx = 0.20 m/s` **STEP** command through the live ROS
chain pitches the free-jointed base from 0 -> **+1.21 rad (~69 deg)** over
~1.2 s, then locks it nose-down with the wheels free-spinning airborne, so the
base delivers only ~0.5x.  The DIRECT path (byte-identical MJCF) never tips;
`mjModel.opt`, the MJCF text, the plugin startup sequence and the control
cadence were all reproduced faithfully and stay flat at ~1.0x.  The trigger is
the **sharpness of the commanded-velocity step** through the live loop.

## The fix — acceleration limit in `omni_base_controller`

File: `src/robot_nav/robot_nav/omni_base_controller.py` (R2).

The controller consumes `/cmd_vel` directly and previously set the wheel
command straight from the Twist, so a step reached full wheel rate in one tick.
It now applies a **trapezoidal ramp on the commanded body velocity**:

- New params: `max_accel` (double, default **4.0 m/s^2**) and
  `max_angular_accel` (double, default **`max_accel / BASE_RADIUS`** ~ 32.0
  rad/s^2).  Both validated `> 0` (raise `ValueError`, same as the existing
  `publish_rate` check).
- `_on_cmd_vel` records the commanded `(vx, vy, wz)` as the ramp **target**;
  `_publish_wheels` slews the current command toward that target each tick by
  at most `max_accel*dt` (linear) / `max_angular_accel*dt` (wz), then runs the
  holonomic IK on the *ramped* value.
- On `cmd_vel_timeout` the **target** becomes `(0, 0, 0)` and the ramp
  decelerates symmetrically (no instant jump to zero — that would be the same
  sharp step in reverse).  The existing "zeroing" warning still logs once when
  the timeout engages.
- The ramp core is a **pure module-level helper**
  `ramp_velocity(current, target, max_accel, max_angular_accel, dt)`, unit
  tested without a ROS graph; a non-positive `dt` returns `current` unchanged.

### Default rationale (`max_accel = 4.0`)

The verification sweep found the tip is deterministic for a ramp time
`R <= 0.01 s`, stochastic at `R = 0.02 s` (2/3 tip), and reliably no-tip for
`R >= 0.03 s` (3/3), solidly safe at `R = 0.05 s` (4/4).  That is a max body
acceleration band of `0.20/0.05 = 4.0` up to `0.20/0.03 = 6.7 m/s^2`.
**4.0 m/s^2 is the conservative end of the verified band** (a 0.05 s ramp to
0.20 m/s), so it is the default.

This is a *physical* parameter (a real base cannot teleport to speed) and is
**distinct from the forbidden vx/vy magnitude cap**: steady-state speed is
unchanged, only the transient is shaped.  `max_angular_accel` has no measured
no-tip bound (the fidelity regression drives pure `+vx`, wz == 0 throughout),
so it is set to the linear limit expressed at the wheel-contact radius
(`max_accel / BASE_RADIUS`) as a simple, documented choice.

## Test changes

`src/robot_bringup/test/test_pr2_navigate.py`:

- `_drive_probe_worker` now also tracks and returns the **max absolute base
  pitch** over the drive (`max_pitch`, from `asin(2*(w*y - z*x))` of the
  `base_link` quaternion, clamped to `[-1, 1]`), via a new
  `_pitch_from_quaternion` helper alongside `_yaw_from_quaternion`.  All
  `_drive_probe` callers were updated for the extra tuple element.
- New constant `MAX_VX_FIDELITY_PITCH = 0.15` rad (healthy ~0.006 rad vs tipped
  ~1.21 rad -> ~25x / ~8x margin), asserted in
  `test_base_delivers_commanded_vx_through_the_ros_chain`.

`src/robot_nav/test/test_omni_ik.py`: seven unit tests for `ramp_velocity`
(step reaches target in 3 ticks with no overshoot; symmetric deceleration; zero
stays zero; on-target fixed point; per-axis limits; non-positive `dt`;
finiteness).

## Red-before-green evidence

**RED** — fidelity test at 0.20 m/s against the **UNMODIFIED** controller
(ramp helper/test present, controller reverted via stash):

```
[pr2-nav] vx fidelity +vx=0.20: dx=0.394 delivered=0.656x dyaw=-0.046 max_travel=0.394 max_pitch=1.232
FAILED ... delivered only 0.656x of the commanded speed (expected >= 0.85x)
```

The base **tipped** (`max_pitch = 1.232 rad`) and under-delivered
(`0.656x < 0.85x`).

**GREEN** — same 0.20 m/s case with the `max_accel` limit in place:

```
[pr2-nav] vx fidelity +vx=0.20: dx=0.600 delivered=1.001x dyaw=-0.005 max_travel=0.600 max_pitch=0.036
PASSED
```

**All three speeds** (`test_base_delivers_commanded_vx_through_the_ros_chain`),
with the limit:

| speed (m/s) | dx (m) | delivered | dyaw (rad) | max_pitch (rad) | result |
|---|---|---|---|---|---|
| 0.10 | 0.314 | 1.045x | -0.006 | 0.064 | PASS |
| 0.14 | 0.377 | 0.897x | -0.012 | 0.016 | PASS |
| 0.20 | 0.595 | 0.992x | -0.015 | 0.017 | PASS |

All three: `delivered >= 0.85x`, `|dyaw| <= 0.30`, `max_pitch <= 0.15`, no tip.

**R4 closed-loop** — `test_navigate_to_pose_converges_within_tolerance`
(goal (0.60, -0.45, yaw -1.0)): `succeeded=True`, `xy_err=0.021 < 0.10`
(no regression from the accel limit).

**Unit tests** — `test_omni_ik.py`: 17 passed (10 existing + 7 new ramp tests).

## Not changed

`_ROLLER_MASS = 0.01` and `_BASE_DIAGINERTIA = '0.1 0.1 0.1'` (R1).  No vx/vy
magnitude cap, no solver/friction/inertia retune, no Nav2 velocity-smoother
reliance (the fidelity test bypasses it).
