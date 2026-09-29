# Implementation — issue #137: rim-roller contact numerics (vx slip at ≥ 0.14 m/s)

## Summary

The mobile base **stalled** under a pure `+vx` command at or above ~0.14 m/s
through the shipped ROS velocity chain. The wheels kept spinning at the full
commanded rate (3.464 rad/s) while the base body moved a few tenths of a metre
and then froze — pure roller-against-floor **contact slip**, not a command drop.

**Fix (one number):** `_ROLLER_MASS` 0.01 → **0.16 kg** in
`src/robot_description/robot_description/mjcf_model.py` (the roller inertia is
derived from it, so no stale tensor). No mitigation, no `vx_max` cap, no
version split, no vendored-source edit. Direct `test_mjcf_drive.py` stays green.

## Pinned R9 cause (R2) — solver-generation delta, not ROS runtime

The cause is **not** a ROS-runtime trigger, a controller-interleave bug, a
timestep problem, or an arm/column-controller effect. It is a **MuJoCo 3.9 vs
3.12 solver delta** for this light-roller-between-heavy-bodies contact.

Evidence (each reproduced by running, not reasoning):

1. **Direct MuJoCo 3.12 @ 0.002 s, vx = 0.20, 3 s and 6 s** (conda mujoco,
   `write_mjcf_model` + `_drive`) → **faithful, ratio ~1.00** for the whole run
   (dx = 1.15 m at 5.75 s). So 3.12 does *not* degrade; the "#132 1.0 s window"
   caveat was an artifact of the 1.0 s default `duration`, not a real decay.

2. **Shipped ROS chain, vx = 0.20, time-resolved probe** → the base ramps to a
   peak then **stalls terminally at dx ≈ 0.40 m** (frozen while
   `base_left_wheel`/`base_right_wheel` keep reporting ±3.464 rad/s and the sim
   clock advances in real time). `base_back_wheel` ≈ 0 (correct for pure +vx).
   Over 3 s dx ≈ 0.40–0.43 → ratio ≈ 0.51–0.67; the base is *stopped*, not slow.

3. **Bisect of the ROS-runtime candidates** — all REFUTED:

   | Candidate | Test | Result |
   |---|---|---|
   | (a) `simulation_frequency` | 250 / 500 / **1000** Hz (0.004 / 0.002 / **0.001** s) | All stall at dx ≈ 0.40–0.43 m. **No timestep dependence** → not solver timestep fragility. |
   | (b) arm/column position actuators | — | Not needed: the stall reproduces **with no ROS at all** (below). |
   | (c) settle delay | 0 / 1 / 3 / 8 s before driving | Shifts *which* attractor is reached (the contact is bistable) but does not remove the stall. |

4. **Decisive isolation — vendored MuJoCo 3.9.0 alone, no ROS.** A C++ harness
   built against the vendored `libmujoco.so` (3.9.0, from the dfki-ric
   FetchContent) loads the *same* derived MJCF and replays the `_drive` protocol
   (settle 1.0 s → ramp 1.5 s → measure). Result at vx = 0.20, 3 s:
   **dx = 0.011 m (ratio 0.019 — full stall)**, identical in character to the
   ROS symptom. The same harness against the 3.12 library is faithful. **No ROS
   process, no controller, no DDS involved** → the trigger is the solver.

**PINNED CAUSE:** the vendored **MuJoCo 3.9.0** solver wedges the
light-roller-between-heavy-bodies contact at its nominal 0.002 s timestep for
the original 0.01 kg roller; MuJoCo 3.12 holds the same MJCF. The original
`_ROLLER_MASS = 0.01` (a 0.01 kg roller against a ~6 kg chassis) sits in a
narrow chaotic pocket of the 3.9 contact solver.

## Levers probed (R3) — all against the vendored 3.9 library

Each lever was probed by generating the real MJCF (`write_mjcf_model` with the
parameter monkeypatched) and replaying the `_drive` protocol in the 3.9 harness.

### Lever 1 — roller mass/inertia (`_ROLLER_MASS`) → **WORKED (the fix)**

Under the shipped `_drive` protocol, 3.9 delivery at vx = 0.20, 3 s:

| `_ROLLER_MASS` | dx ratio | steady-speed ratio |
|---|---|---|
| 0.010 (original) | **0.019** | 0.001 (stall) |
| 0.02 | 1.007 | 1.008 |
| 0.05 | 1.010 | 1.041 |
| 0.10 | 1.020 | 1.020 |
| 0.16 (chosen) | 1.009 | 1.009 |
| 0.20 | 0.931 | 0.948 |

Every mass from 0.02 to 0.20 delivers ~1.0×; **only the exact original 0.01
value wedges.** `_ROLLER_MASS = 0.16` was chosen as a **deliberately interior**
value with margin on both sides.

Chosen value validated across the grid (vendored 3.9, `_drive` protocol):

- `vx` ∈ {0.02, 0.05, 0.10, 0.14, 0.20, 0.22, 0.25, 0.30, 0.35, 0.40, 0.50, 0.60}
  → ratios **0.98–1.01**, no stall (well beyond the 0.20 target).
- durations {2, 3, 6, 10, 12} s → ratios 0.99–1.01.
- A near-baseline sweep (0.0105, 0.011, 0.012, 0.015, 0.019) shows the stall
  reappears only in a narrow pocket around 0.011 → confirms the mechanism and
  that 0.16 is far from it.

### Lever 2 — contact solref/solimp → NOT used (worse)

- Floor `solref` 0.02→0.01 alone also stabilizes at vx = 0.20, but degrades the
  low-speed cases (vx = 0.10 → 0.85×), and the response is non-monotone
  (0.01 OK, 0.02 stalls, 0.04 OK, 0.08 OK) — a chaotic boundary, not a robust
  fix. Larger `dampratio` (2.0) made it worse.
- Overlay-default `solref` change destabilized the plant (dx went negative).
- **Rejected** in favour of the single clean mass retune.

### Lever 3 — solver iterations → NO EFFECT (refuted)

Injecting `<option integrator="implicit" iterations="200|500"
ls_iterations="200|500"/>` produced **byte-identical** results to the baseline
stall → the stall is not an iteration-budget limit.

## Final values

| Constant | Before | After |
|---|---|---|
| `_ROLLER_MASS` | 0.01 kg | **0.16 kg** |
| `_ROLLER_I_AXIS` (derived) | 2.45e-7 | 3.92e-6 |
| `_ROLLER_I_TRANS` (derived) | 4.56e-7 | 7.29e-6 |

(`_ROLLER_RADIUS`, `_ROLLER_HALF_LENGTH`, solref/solimp, iterations all
unchanged.)

## Empirical ROS-chain delivery (R4) — post-fix, shipped `mujoco.launch.py`

Measured by the new regression test
(`test_base_delivers_commanded_vx_through_the_ros_chain`), ground-truth
`GetBodyState('base_link')`, 3 s per speed, fresh sim session per speed:

| commanded vx | dx over 3 s | delivered | dyaw |
|---|---|---|---|
| 0.10 m/s | 0.299 m | **0.996×** | −0.001 rad |
| 0.14 m/s | 0.420 m | **1.001×** | 0.000 rad |
| 0.20 m/s | 0.597 m | **0.994×** | −0.000 rad |

Pre-fix (same chain): vx = 0.20 delivered ≈ 0.2× and then froze (dx ≈ 0.40 m,
base stalled while wheels spun). A 6 s confirmation run measured
1.000× / 1.005× / 1.008× at 0.20 / 0.14 / 0.10 with |dyaw| ≈ 0.005 rad.

## Direct path (3.12) — unchanged behaviour

`pixi run python -m pytest src/robot_description/test/test_mjcf_drive.py` →
**21 passed** with `_ROLLER_MASS = 0.16`. Direct `_drive` at vx = 0.10 / 0.14 /
0.20 gave ratios 1.014 / 1.003 / 1.009; the 6 s trace stays ~1.0× throughout.
The existing assertions (pure-channel ~1.0× at vx = 0.3, combined vx+wz
composition, contact-slip bound, roller-hinge rolling) all still hold.

## Files changed

- `src/robot_description/robot_description/mjcf_model.py` — `_ROLLER_MASS`
  0.01 → 0.16 (+ comment recording the pinned cause).
- `src/robot_bringup/test/test_pr2_navigate.py` — new parametrized regression
  `test_base_delivers_commanded_vx_through_the_ros_chain` at 0.10/0.14/0.20 m/s,
  3 s each, ≥ 0.85× delivery + bounded yaw, fresh session per speed.
- `scripts/test_baseline.json` — ratcheted `robot_bringup` count (3 new probes).

## Notes / residual

- The 3.9 contact is **bistable**: the pre-command microstate selects a rolling
  or a wedged attractor. The fix moves the plant firmly onto the rolling
  attractor across the whole operating envelope; it does not (and need not)
  eliminate the bistability.
- `simulation_frequency` was left at the shipped 500 Hz.
